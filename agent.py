"""DispatchDesk agent: read the tickets with rules, schedule in code.

Reading (no model in the loop):

* **Negated urgency.** Half the P3 tickets are written "no need to hurry, it is not
  critical" - a substring match on "hurry"/"critical" reads them as emergencies. So the
  calm phrasings are checked FIRST and only then the alarm words.
* **The decoy trade.** Vague tickets describe a symptom in the vocabulary of the wrong
  trade, and usually say out loud that the obvious trade has been ruled out ("power is
  fine"). Those exonerations remove a trade, and a few cause-level rules catch the rest.
* **The decoy place.** "Our driver is coming from Kista" names a zone that is not the
  site. A zone is only believed from the directory (name or nickname), the game, or a
  repeat from a named customer; a zone named in the prose is struck off the candidates.

Scheduling (level 2 has twice the work the fleet can do, so saying no is the job):

* Re-plan every tick, earliest SLA deadline first. Each job goes to the technician with
  the best expected net (value by the chance of finishing inside the SLA, minus travel,
  overtime and the minutes it ties them up). When the fleet is overloaded a job must also
  pay for those minutes, or it is refused.
* A P1 that would be late recalls a technician on the road to lower-priority work.
* An unknown P1/P2 site is probed at the zone that minimises the expected detour, and
  asked about when the answer lands before the technician sets off.
* Repeats are never dispatched; merged only when the original is certain (a merge earns
  nothing, a wrong one costs 15).
"""
import random
import re
import time

try:
    import llm_reader
except Exception:                                   # no helper file: rules only
    llm_reader = None
from common import (dd_now, dd_parse_directory, dd_parse_jobs, dd_parse_messages,
                    dd_parse_techs, dd_travel, detect_game)

# A trade is ruled out when the customer says the obvious suspect already checked.
EXONERATE = [
    ("the network guys say their kit is fine", "IT"), ("kit is fine", "IT"),
    ("power is fine", "electrical"), ("the lights are on", "electrical"),
    ("nothing to do with the pipes", "plumbing"), ("water side looks fine", "plumbing"),
    ("the electrician we called said it is not his thing", "electrical"),
    ("said it is not his thing", "electrical"),
    ("the fridges themselves are cold", "refrigeration"), ("fridge itself is fine", "refrigeration"),
    ("the lift company says it is our supply", "lift"), ("says it is our supply", "lift"),
]

# Cause-level rules, strongest first. Each is (phrases, trade).
RULES = [
    (["all dropped at once", "the same wall", "at the same second", "smells burnt",
      "keep tripping the board", "one phase is dead", "breaker keeps tripping",
      "distribution board", "sparking", "shaft go out", "is our supply"], "electrical"),
    (["card readers", "monitoring dashboard", "dashboard says", "lost their connection",
      "no connection", "access-point", "core router", "network switch", "point-of-sale",
      "cannot reach the server"], "IT"),
    (["condensation", "ceiling cassette", "thermostats show", "never start", "roasting",
      "boiling", "not holding the required temperature", "keeps cycling", "air conditioning", "the ac ", "ac unit",
      "ventilation", "heat pump", "stuffy"], "HVAC"),
    (["cold room", "walk-in", "freezer", "blast chiller", "ice cream cabinet",
      "vaccine storage", "display fridge", "nothing gets cold", "temperature is climbing"], "refrigeration"),
    (["stuck between floors", "goods hoist", "goods lift", "wheelchair ramp lift", "the cabin",
      "jammed halfway", "out of service", "skips the 4th floor", "doors won't close",
      "lift 2", "elevator"], "lift"),
    (["pipe burst", "backing up", "no hot water", "inlet valve", "sprinkler test valve",
      "dishwasher inlet", "drain in the cold room", "overflowing", "water running down",
      "spraying water", "standing in a puddle", "water on the floor"], "plumbing"),
]

# Calm phrasings are tested before alarm words, so a negated "not critical" cannot read as P1.
CALM = ["no rush", "not urgent", "no need to hurry", "not critical", "not an emergency",
        "low priority", "whenever you have a slot", "whenever convenient", "just logging it",
        "end of day is fine", "before the end of the day", "do not forget us", "workaround"]
ALARM = ["within the hour", "right now", "urgent", "critical", "please hurry",
         "getting worse by the minute", "someone is inside", "standing still",
         "before they are lost", "only way up", "patients"]
TODAY = ["today", "this morning", "early afternoon", "prioritise", "disrupting",
         "affecting half the floor", "cope until"]
NOT_A_JOB = ["invoice", "wrong number", "sandwiches", "no action needed", "nothing broken",
             "nothing is broken", "no fault to report", "technician visited", "for your records",
             "fixed it yesterday", "expires next month", "closing the loop", "sorted it out",
             "email a quote", "door code changes", "moved reception"]
FOLLOW_UP = ["hi again", "following up", "2nd message", "re:", "still nobody", "any update",
             "is this being handled", "has anyone been dispatched", "same building",
             "still ongoing", "wrote earlier", "my colleague"]
# What a follow-up says it is about ("still nobody here about the power issue").
TOPIC = [(["air con", "aircon", "air conditioning"], "HVAC"), (["fridge", "cold room", "freezer"], "refrigeration"),
         (["power", "electric"], "electrical"), (["network", "wifi", "internet"], "IT"),
         (["lift", "elevator"], "lift"), (["water", "leak", "pipe"], "plumbing")]
DURATION_GUESS = {"HVAC": 60, "refrigeration": 60, "electrical": 45, "IT": 40, "lift": 75,
                  "plumbing": 50}
MAX_QUEUE = 2
# What a minute of technician time is worth to the work still to come. Charging it when
# choosing a technician sends each job to whoever finishes it soonest; charging it when
# deciding whether to take a job at all only pays when the fleet is overloaded (level 2 has
# twice the work it can do), so there it scales with the load: nothing below LOAD_LO of the
# remaining capacity, the full OPP from LOAD_HI up.
OPP = 0.2
LOAD_LO, LOAD_HI = 0.6, 1.0
LOAD_UNTIL = 360                # tickets stopped arriving around 14:00 on the practice shifts
WRONG_SITE_MIN = 15             # a visit to the wrong site costs the technician 15 minutes
# How often each trade came from each zone over practice shifts 1-50 (levels 1 and 2, the
# six-zone city). Only used while the travel table shows those zones; any other city falls
# back to weighting each zone by the number of directory customers in it.
ZONE_PRIOR = {
    "electrical": {"Solna": .24, "Sodermalm": .23, "Sundbyberg": .23, "Nacka": .19, "Kista": .11},
    "plumbing": {"Sundbyberg": .50, "Sodermalm": .29, "Nacka": .21},
    "HVAC": {"Solna": .36, "Sodermalm": .21, "Sundbyberg": .16, "Kista": .15, "Nacka": .12},
    "IT": {"Kista": .36, "Solna": .31, "Nacka": .16, "Sodermalm": .16},
    "refrigeration": {"Nacka": .28, "Sodermalm": .26, "Sundbyberg": .26, "Kista": .20},
    "lift": {"Kista": .43, "Nacka": .31, "Solna": .26},
}
ZONE_PRIOR_ZONES = {"Kista", "Solna", "Sundbyberg", "Sodermalm", "Nacka"}
# Work takes 30 to 90 minutes "depending on the fault" (rules); the spread seen per trade on
# practice shifts. Used to weigh the chance a job finishes inside its SLA, not just the mean.
DURATION_SPREAD = {"HVAC": (30, 90), "refrigeration": (30, 90), "electrical": (30, 60), "IT": (30, 60),
                   "lift": (60, 90), "plumbing": (30, 60)}

# The rules text at the top of every episode is the authority; these are only what it said
# on levels 1-4, used when a value cannot be read from it (the final round changes a rule).
DEFAULT_RULES = {
    "value": {1: 100, 2: 40, 3: 10},
    "sla": {1: 120, 2: 240, 3: None},   # minutes after the message; None = by shift end
    "late": 0.5,                        # share of the value earned after the SLA
    "travel": 0.5,                      # per minute on the road
    "overtime": 1.0,                    # per minute after a technician's own shift end
    "fee": 5.0,                         # per question
    "merge": 15.0,                      # per wrong merge
    "shift_end": 540,                   # 17:00, minutes after 08:00
    "hard_stop": 660,                   # 19:00, nobody works later
    "tick": 30,
}


def _hhmm(h, m):
    return (int(h) - 8) * 60 + int(m)


def parse_rules(obs):
    """Read values, SLAs and costs from the rules text (everything before the first board)."""
    r = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_RULES.items()}
    head = obs[:obs.find("=== ")] if "=== " in obs else obs
    num = r"(\d+(?:\.\d+)?)"
    m = re.search(r"Job value[^\n]*", head)
    if m:
        for prio, val in re.findall(r"P([123])\s*=\s*" + num, m.group(0)):
            r["value"][int(prio)] = float(val) if "." in val else int(val)
    for prio in (1, 2):
        m = re.search(r"P%d (?:must be finished |finished )?within %s\s*(h|hours?|min|minutes?)\b" % (prio, num), head)
        if m:
            r["sla"][prio] = int(round(float(m.group(1)) * (60 if m.group(2).startswith("h") else 1)))
    for key, pattern in (("late", r"earns %s\s*%%" % num), ("travel", r"travel %s per minute" % num),
                         ("overtime", r"overtime %s per minute" % num), ("fee", r"question fee %s" % num),
                         ("merge", r"wrong merge %s" % num)):
        m = re.search(pattern, head)
        if m:
            v = float(m.group(1))
            r[key] = v / 100.0 if key == "late" else v
    m = re.search(r"one shift, \d\d:\d\d-(\d\d):(\d\d)", head)
    if m:
        r["shift_end"] = _hhmm(*m.groups())
    m = re.search(r"works past (\d\d):(\d\d)", head)
    if m:
        r["hard_stop"] = _hhmm(*m.groups())
    m = re.search(r"ticks of (\d+) minutes", head)
    if m:
        r["tick"] = int(m.group(1))
    return r


BOARD_HEAD = re.compile(r"=== \d\d:\d\d \| tick \d+/\d+ \|")
# the message refers to a place ("calling from X", "here at X", "(X)") that the directory
# could not resolve: the one site question a model can answer and the rules cannot
SITE_REF = re.compile(r"\b(calling from|here at|we are at|this is .{2,30} again)\b|\(")
LLM_FILL = 0.6              # model confidence needed to fill a priority or trade the rules left open
LLM_SURE = 0.9              # ... to take a site, or to close a ticket as not a job


class Agent:
    def __init__(self):
        self.rng = random.Random(7)
        self.started = time.monotonic()
        try:
            self.llm = llm_reader.client() if llm_reader else None
        except Exception:
            self.llm = None

    def reset(self, game_id, player_id):
        self.game_id = game_id

    def __call__(self, observation):
        # Never raise: a raised reply is a fault, and four faults lose the shift.
        self.started = time.monotonic()
        try:
            observation = observation.replace("\r\n", "\n")
            # the intro may be missing from a cut history; a board header is enough to play
            if detect_game(observation) != "dispatchdesk" and not BOARD_HEAD.search(observation):
                return "[wait]"
            return self._shift(observation)
        except Exception:
            return "[wait]"

    # ------------------------------------------------------------------ reading
    def _site(self, blob, directory):
        """Only a directory match counts. Prose place names are decoys often enough."""
        for c in directory:
            if c["name"].lower() in blob:
                return c["zone"]
        for c in directory:
            if any(a.lower() in blob for a in c["aliases"]):
                return c["zone"]
        return None

    def _customer(self, blob, directory):
        for c in directory:
            if c["name"].lower() in blob:
                return c["name"]
        for c in directory:
            if any(a.lower() in blob for a in c["aliases"]):
                return c["name"]
        return None

    def _trade(self, blob):
        ruled_out = {trade for phrase, trade in EXONERATE if phrase in blob}
        for phrases, trade in RULES:
            if trade in ruled_out:
                continue
            if any(p in blob for p in phrases):
                return trade
        for phrases, trade in RULES:              # second pass, ignoring the exonerations
            if any(p in blob for p in phrases):
                return trade
        return None                               # unreadable: ask the customer

    def _priority(self, blob):
        if any(p in blob for p in CALM):
            return 3
        if any(p in blob for p in ALARM):
            return 1
        if any(p in blob for p in TODAY):
            return 2
        return None                               # unreadable: ask the customer

    # ------------------------------------------------------------------ the shift
    def _shift(self, obs):
        jobs = dd_parse_jobs(obs)
        techs = dd_parse_techs(obs)
        travel = dd_travel(obs)
        directory = dd_parse_directory(obs)
        msgs = dd_parse_messages(obs)
        now, tick, ticks = dd_now(obs)
        rules = parse_rules(obs)
        shift_end = rules["shift_end"]
        byid = {j["jid"]: j for j in jobs}
        # customer sites: a job whose site is unknown is at one of these, never at the depot
        sites = sorted({c["zone"] for c in directory if c["zone"] in travel}) \
            or [z for z in travel if z != "Depot"] or list(travel)

        # Only our own replies count as "asked": the rules text and the board footer both
        # print example asks ("[ask J7 location]", "[ask J4 location]").
        mine = " ".join(l for l in obs.splitlines() if l.startswith("[Player]"))
        asked = lambda jid, topic: "[ask %s %s]" % (jid, topic) in mine

        # ---- repeats first: they can tell us where an anonymous original is
        closes, merges, asks, asks_p3, plans = [], [], [], [], []
        hints, repeats = {}, set()
        for j in jobs:
            if j["status"] != "unassigned":
                continue
            blob = self._blob(j, msgs)
            if any(p in blob for p in NOT_A_JOB):
                closes.append("[ignore %s]" % j["jid"])
                repeats.add(j["jid"])
                continue
            if not any(p in blob for p in FOLLOW_UP):
                continue
            # The customer answered the problem question and it is not "the same issue":
            # a new job after all, read it like any other.
            if asked(j["jid"], "problem") and j["skill"] and not re.search(
                    r"about %s \(problem\): \"yes - this is the same issue" % j["jid"], obs.lower()):
                continue
            repeats.add(j["jid"])                 # a likely repeat is never dispatched
            orig, hint, candidates = self._original(j, blob, jobs, msgs, directory, obs)
            if orig:
                merges.append("[merge %s %s]" % (j["jid"], orig))
            elif hint:
                hints.setdefault(hint[0], hint[1])
            elif not candidates and self._customer(blob, directory) is None \
                    and not asked(j["jid"], "problem"):
                asks_p3.append("[ask %s problem]" % j["jid"])   # unclear which job it follows: ask once

        # ---- the model, only where the rules are unsure (and one health check per shift)
        cache, notes = {}, []
        try:
            cache, notes = self._consult(jobs, msgs, directory, techs, sites, repeats, obs)
        except Exception:
            pass

        # ---- read every real job still waiting for a technician
        for j in jobs:
            if j["status"] != "unassigned" or j["jid"] in repeats:
                continue
            blob = self._blob(j, msgs)
            zone = j["zone"] or self._site(blob, directory) or hints.get(j["jid"])
            trade = j["skill"] or self._trade(blob)
            prio = j["prio"] or self._priority(blob)
            llm = cache.get(j["jid"])
            if llm:
                if llm["not_a_job"] and llm["confidence"] >= LLM_SURE and trade is None and prio is None:
                    closes.append("[ignore %s]" % j["jid"])  # the rules could read nothing either
                    continue
                zone, trade, prio = self._with_model(j, llm, zone, trade, prio, blob, directory, sites, msgs)
            # Unreadable trade or urgency: ask, and hold the job until the answer is in; after
            # it, fall back to defaults. An unknown site is not held: it is probed (below).
            need = [t for v, t in ((trade, "problem"), (prio, "urgency")) if v is None and not asked(j["jid"], t)]
            if need:
                if prio is None or rules["value"].get(prio, 0) > rules["fee"] * len(need):
                    for topic in need:
                        (asks if prio in (1, 2, None) else asks_p3).append("[ask %s %s]" % (j["jid"], topic))
                continue
            trade = trade or "electrical"
            prio = prio or 2
            if zone is None and prio >= 3:
                # A P3 is not worth a probe: a wrong site ties a technician up for 10 points.
                # Ask when the fee plus the likely drive still leaves something, and hold it.
                if not asked(j["jid"], "location"):
                    crew = [t for t in techs if not t["off_shift"] and trade in t["skills"]]
                    drive = (sum(min(travel.get(t["dest"] or t["zone"], {}).get(z, 30) for t in crew)
                                 for z in sites) / len(sites)) if crew else 99
                    if rules["value"].get(prio, 0) - rules["fee"] - rules["travel"] * drive > 0:
                        asks_p3.append("[ask %s location]" % j["jid"])
                continue
            plans.append(self._plan(j, zone, trade, prio, blob, directory, sites, rules))

        # ---- re-plan every tick: queued jobs (nobody on the road yet) rejoin the pool, so a
        # new P1 can jump ahead of queued P3 work
        current = {t["tid"]: list(t["queue"]) for t in techs}
        replannable = set()
        for j in jobs:
            if j["status"] != "queued":
                continue
            blob = self._blob(j, msgs)
            zone = j["zone"] or self._site(blob, directory) or hints.get(j["jid"])
            trade = j["skill"] or self._trade(blob) or "electrical"
            prio = j["prio"] or self._priority(blob) or 2
            plans.append(self._plan(j, zone, trade, prio, blob, directory, sites, rules))
            replannable.add(j["jid"])

        for t in techs:
            # a queued job we could not re-plan stays where it is, in front
            t["queue"] = [(jid, None) for jid in t["queue"] if jid not in replannable]
            t["at"] = t["dest"] or t["zone"]
            t["free_at"] = max(now, t["until"] or now) + 45 * len(t["queue"])
            cur = byid.get(t["job"])
            if t["activity"] == "travel":         # the job they are driving to still has to be done
                t["free_at"] += DURATION_GUESS.get(cur and (cur["skill"] or self._trade(self._blob(cur, msgs))), 45)
            elif t["activity"] == "wasted" and cur and cur["status"] == "rerouting" and cur["zone"]:
                # at a wrong site: after the 15 minutes they drive on to the true one and work
                t["free_at"] += travel.get(t["zone"], {}).get(cur["zone"], 30) \
                    + DURATION_GUESS.get(cur["skill"] or self._trade(self._blob(cur, msgs)), 45)
                t["at"] = cur["zone"]

        # ---- how loaded the fleet is: the work waiting (and, at the rate tickets have come in
        # so far, still to come) against the technician minutes left. Refusing work only pays
        # when there is more of it than the fleet can do.
        self.load = self._load(plans, techs, msgs, now, rules)

        # ---- earliest deadline first, a P1 ahead of a P3 at the same deadline
        plans.sort(key=lambda p: (p["deadline"], p["prio"]))
        recalls = []
        k = 0
        while k < len(plans):
            p = plans[k]
            k += 1
            # Pick the technician with the best expected net: value earned (half if past the
            # SLA, nothing past the 19:00 hard stop) minus travel, their own overtime and the
            # time it ties them up. A job nobody can do at a profit is left alone.
            best = None
            for t in techs:
                if p["trade"] not in t["skills"] or t["off_shift"] or len(t["queue"]) >= MAX_QUEUE:
                    continue
                if t["at"] not in travel:
                    continue
                opt = self._score(t, t["free_at"], t["at"], p, travel, rules)
                if opt["accept"] > 0 and (best is None or opt["net"] > best["net"]):
                    best = opt
            # A P1 that will be late: a technician on the road to lower-priority work can be
            # recalled. They still reach that site (the drive is sunk), take the P1 from there,
            # and the recalled job goes back into the pool.
            if p["prio"] == 1 and (best is None or best["on_time"] < 1):
                for t in techs:
                    cur = byid.get(t["job"])
                    if t["activity"] != "travel" or not cur or cur["status"] != "travelling" or t["queue"] \
                            or p["trade"] not in t["skills"] or t["off_shift"] or not t["dest"] or not t["until"]:
                        continue
                    cblob = self._blob(cur, msgs)
                    cprio = cur["prio"] or self._priority(cblob) or 2
                    if cprio == 1 or cur["jid"] in repeats:
                        continue
                    opt = self._score(t, max(now, t["until"]), t["dest"], p, travel, rules)
                    if opt["on_time"] >= 1 and opt["accept"] > 0 and (best is None or opt["net"] > best["net"]):
                        best = dict(opt, recall=cur, cprio=cprio, cblob=cblob)
            p["tech"] = best and best["tech"]
            if best is None:
                continue
            t = best["tech"]
            if best.get("recall"):
                cur = best["recall"]
                recalls.append("[unassign %s]" % cur["jid"])
                t["free_at"], t["at"] = max(now, t["until"]), t["dest"]
                back = self._plan(cur, cur["zone"] or t["dest"], cur["skill"] or self._trade(best["cblob"]) or "electrical",
                                  best["cprio"], best["cblob"], directory, sites, rules)
                back["job"] = dict(cur, status="unassigned")
                plans.insert(k, back)
                plans[k:] = sorted(plans[k:], key=lambda p: (p["deadline"], p["prio"]))
                best = self._score(t, t["free_at"], t["at"], p, travel, rules)
            t["queue"] = t["queue"] + [(p["job"]["jid"], best["zone"])]
            p["departs"] = t["free_at"]
            t["free_at"] = best["finish"]
            t["at"] = p["zone"] or best["zone"]

        # ---- an unknown site: ask when the answer lands before the technician sets off (the
        # game sends them to the confirmed site then), or when nobody can take a P1/P2 yet
        for p in plans:
            jid = p["job"]["jid"]
            if p["zone"] is not None or asked(jid, "location"):
                continue
            if p["tech"] is not None and p["departs"] >= now + rules["tick"]:
                (asks if p["prio"] in (1, 2) else asks_p3).append("[ask %s location]" % jid)
            elif p["tech"] is None and p["prio"] in (1, 2) and p["job"]["status"] == "unassigned":
                asks.append("[ask %s location]" % jid)

        # ---- send only the differences. Re-assigning a queued job moves it to the back of the
        # named queue, so everything after the first change in a queue is re-sent in order.
        prio_of = {p["job"]["jid"]: p["prio"] for p in plans}
        planned = {jid for t in techs for jid, _ in t["queue"]}
        urgent, later = [], []
        for t in techs:
            want = t["queue"]
            have = current[t["tid"]]
            k = 0
            while k < len(want) and k < len(have) and want[k][0] == have[k]:
                k += 1
            cmds = ["[unassign %s]" % jid for jid in have[k:] if jid in replannable and jid not in planned]
            cmds += ["[assign %s %s %s]" % (t["tid"], jid, zone) for jid, zone in want[k:] if zone]
            if cmds:
                top = min([prio_of.get(jid, 3) for jid, _ in want[k:]] + [3])
                (urgent if top <= 2 else later).append((top, cmds))
            t["queue"] = [jid for jid, _ in want]
        urgent = recalls + [c for _, cs in sorted(urgent, key=lambda x: x[0]) for c in cs]
        later = [c for _, cs in later for c in cs]

        # P1/P2 dispatches first, then questions, then P3 dispatches; closing repeats and
        # non-requests costs nothing to put off a tick. At most four, and always [wait].
        cmds = urgent + asks + asks_p3 + later + merges + closes
        # model answers ride along outside the brackets, so a restarted agent still has them
        head = ["llm " + " ".join(notes) + " ;"] if notes else []
        return " ".join(head + cmds[:4] + ["[wait]"])

    # ------------------------------------------------------------------ the model
    def _consult(self, jobs, msgs, directory, techs, sites, repeats, obs):
        """Ask the model about each new ticket the rules are unsure of, in parallel, within
        the tick budget. Returns ({jid: reading}, notes for this reply)."""
        if self.llm is None or llm_reader is None:
            return {}, []
        cache = llm_reader.decode(obs)
        if not llm_reader.healthy(cache):
            return cache, []
        trades = sorted({s for t in techs for s in t["skills"]})
        todo = []
        for j in jobs:
            if j["status"] != "unassigned" or j["jid"] in repeats or j["jid"] in cache:
                continue
            blob = self._blob(j, msgs)
            text = msgs.get(j["jid"], {}).get("text", "").lower()
            unsure = (j["prio"] is None and self._priority(blob) is None) \
                or (j["skill"] is None and self._trade(blob) is None) \
                or (j["zone"] is None and self._site(blob, directory) is None and SITE_REF.search(text))
            # one call per shift on the first ticket, whatever the rules read: it proves the
            # gateway works and prices a call, and it can only fill gaps the rules left
            if unsure or (not cache and not todo):
                m = msgs.get(j["jid"], {})
                todo.append((j["jid"], llm_reader.prompt(j["jid"], m.get("sender", j["sender"]), m.get("text", ""),
                                                         directory, trades, sites, []), set()))
        todo = todo[:max(0, llm_reader.MAX_CALLS - len(cache))]
        got, notes = llm_reader.consult(self.llm, todo, trades, sites, self.started)
        cache.update(got)
        return cache, notes

    def _with_model(self, j, llm, zone, trade, prio, blob, directory, sites, msgs):
        """Fill only what the rules left open, never what they read or the game confirmed. A
        site is only taken when the message refers to a place the directory could not
        resolve, and never a zone the message names in passing (a decoy)."""
        c = llm["confidence"]
        text = msgs.get(j["jid"], {}).get("text", "").lower()
        if zone is None and llm["zone"] and c >= LLM_SURE and SITE_REF.search(text) \
                and llm["zone"] in self._zone_odds(llm["trade"] or trade, blob, directory, sites):
            zone = llm["zone"]
        if trade is None and llm["trade"] and c >= LLM_FILL:
            trade = llm["trade"]
        if prio is None and llm["prio"] in (1, 2, 3) and c >= LLM_FILL:
            prio = llm["prio"]
        return zone, trade, prio

    def _score(self, t, free_at, at, p, travel, rules):
        """What sending technician t (free at `free_at`, standing at `at`) to plan p is worth."""
        duration = DURATION_GUESS[p["trade"]]
        lo, hi = DURATION_SPREAD.get(p["trade"], (duration, duration))
        zone, drive, delay = self._route(at, p, travel)
        arrival = free_at + drive + delay
        finish = arrival + duration
        own_end = t["shift_end"] or rules["shift_end"]
        # expected value over the likely durations: a finish a few minutes past the SLA is a
        # coin flip, not a certainty either way
        on_time = sum(arrival + d <= p["deadline"] for d in (lo, duration, hi)) / 3.0
        value = rules["value"].get(p["prio"], 0) * (on_time + (1 - on_time) * rules["late"])
        if finish > rules["hard_stop"]:
            value = 0.0
        overtime = max(0, finish - max(own_end, free_at))
        base = value - rules["travel"] * drive - rules["overtime"] * overtime
        return {"tech": t, "zone": zone, "finish": finish, "on_time": on_time,
                "net": base - self._opp(p) * (finish - free_at),
                "accept": base - self._accept_opp() * (finish - free_at)}

    def _plan(self, j, zone, trade, prio, blob, directory, sites, rules):
        sla = rules["sla"].get(prio)
        deadline = rules["shift_end"] if sla is None else min(j["minute"] + sla, rules["shift_end"])
        p = {"job": j, "zone": zone, "trade": trade, "prio": prio, "deadline": deadline, "tech": None}
        if zone is None:
            p["cands"] = self._zone_odds(trade, blob, directory, sites)
        return p

    @staticmethod
    def _zone_odds(trade, blob, directory, sites):
        """Where an anonymous job probably is: the customer sites, weighted by how often this
        trade came from each on the practice shifts, minus any place the message names (a
        place named in the prose is never the site: "our driver is coming from Kista")."""
        prior = ZONE_PRIOR.get(trade, {})
        known = bool(prior) and set(sites) <= ZONE_PRIOR_ZONES
        odds = {}
        for z in sites:
            if re.search(r"\b%s\b" % re.escape(z.lower()), blob):
                continue
            w = prior.get(z, 0.0) if known else sum(1 for c in directory if c["zone"] == z) or 1
            if w > 0:
                odds[z] = w
        if not odds:
            odds = {z: 1.0 for z in sites}
        total = sum(odds.values())
        return {z: w / total for z, w in odds.items()}

    @staticmethod
    def _route(at, p, travel):
        """(zone to send them to, expected minutes on the road, expected minutes lost at a
        wrong site) for a technician standing at `at`."""
        if p["zone"] is not None:
            return p["zone"], travel.get(at, {}).get(p["zone"], 30), 0.0
        best = None
        for z, pz in p["cands"].items():
            onward = sum(q * travel.get(z, {}).get(z2, 30) for z2, q in p["cands"].items() if z2 != z)
            drive = travel.get(at, {}).get(z, 30) + onward
            delay = (1 - pz) * WRONG_SITE_MIN
            if best is None or drive + delay < best[1] + best[2]:
                best = (z, drive, delay)
        return best

    def _accept_opp(self):
        return OPP * min(1.0, max(0.0, (self.load - LOAD_LO) / (LOAD_HI - LOAD_LO)))

    @staticmethod
    def _load(plans, techs, msgs, now, rules):
        work = sum(DURATION_GUESS.get(p["trade"], 50) + 20 for p in plans)
        for t in techs:
            if t["activity"] in ("travel", "work", "wasted") and t["until"]:
                work += max(0, t["until"] - now)
        # tickets still to come, at the rate they have arrived so far (about 4 in 5 are real
        # jobs, each about 70 minutes of work and driving)
        rate = len(msgs) / float(max(60, now + 30))
        work += max(0, LOAD_UNTIL - now) * rate * 0.8 * 70
        cap = sum(max(0, (t["shift_end"] or rules["shift_end"]) - now) for t in techs if not t["off_shift"])
        return work / max(1.0, cap)

    @staticmethod
    def _opp(p):
        """What a minute of technician time is worth to the work still to come."""
        return OPP

    def _blob(self, j, msgs):
        """Sender and text, lower-cased. The job row cuts the sender at 28 characters, so
        take it from the message line where it is printed in full."""
        m = msgs.get(j["jid"], {})
        return (m.get("sender", j["sender"]) + " " + m.get("text", "")).lower()

    def _original(self, dup, blob, jobs, msgs, directory, obs=""):
        """What this repeat follows up: (job to merge into, (anonymous job, its zone), the
        candidates). Merging only closes the repeat, it earns nothing, while a wrong merge
        costs 15. So merge only when exactly one open job of the same customer matches what
        the follow-up is about; otherwise leave the repeat alone (it is never dispatched).
        When the original was anonymous, the repeat tells us where it is."""
        who = self._customer(blob, directory)
        topic = next((trade for words, trade in TOPIC if any(w in blob for w in words)), None)
        named, anonymous = [], []
        for j in jobs:
            if j["jid"] == dup["jid"] or j["minute"] > dup["minute"]:
                continue
            if "[merge %s %s] wrong" % (dup["jid"], j["jid"]) in obs:
                continue                          # the game already said no
            other = self._blob(j, msgs)
            # never merge into a non-request (we ignore it) or into another repeat
            if any(p in other for p in NOT_A_JOB) or any(p in other for p in FOLLOW_UP):
                continue
            if topic and (j["skill"] or self._trade(other)) != topic:
                continue
            owner = self._customer(other, directory)
            if who is not None and owner == who:
                named.append(j)
            elif owner is None and j["zone"] is None:
                anonymous.append(j)
        if len(named) == 1:
            return named[0]["jid"], None, named
        if not named and len(anonymous) == 1 and who is not None:
            zone = next(c["zone"] for c in directory if c["name"] == who)
            return None, (anonymous[0]["jid"], zone), anonymous
        return None, None, named + anonymous

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

* Re-plan every tick over the whole horizon: each job is inserted where it adds most to
  the technicians' routes (value by the chance of finishing inside the SLA, minus travel,
  overtime and, when the fleet is overloaded, the minutes it ties them up); a job that adds
  nothing is refused. Only the head of each route is sent to the game.
* An unknown P1/P2 site is probed at the zone that minimises the expected detour, and
  asked about when the answer lands before the technician sets off; for a P2 the route
  planner also weighs asking now and setting off next tick straight to the right site.
* Repeats are never dispatched; merged only when the original is certain (a merge earns
  nothing, a wrong one costs 15).

Layers: this file is the general core; llm_reader.py reads every new ticket once with
arena-small (combined with the regex reader in `_combine`); handlers.py holds the edge-case
handlers; guards.py filters every reply to legal commands.
"""
import os as _os
import random
import re
import sys
import time

import guards
import handlers
try:
    import llm_reader
    LLM_IMPORT_ERROR = None
except Exception as _e:                             # reported below, never silent
    llm_reader = None
    LLM_IMPORT_ERROR = type(_e).__name__
    print("agent: llm_reader import failed: %s: %s" % (type(_e).__name__, _e), file=sys.stderr)
# The board parsers: our own tested copy of the reference `common` (dd_common.py), with the
# container's `common` as a fallback for a board our copy cannot read. The two are the same
# file on the laptop; in the competition container `common` is the organisers' copy.
import dd_common as _parsers_local
try:
    import common as _parsers_cluster
except Exception:                                   # laptop without the harness on the path
    _parsers_cluster = None


def _parsers(obs):
    if _parsers_cluster is not None and _parsers_cluster is not _parsers_local:
        try:
            if not _parsers_local.dd_parse_techs(obs) and _parsers_cluster.dd_parse_techs(obs):
                return _parsers_cluster
        except Exception:
            pass
    return _parsers_local


def detect_game(obs):
    return _parsers_local.detect_game(obs)

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
MAX_QUEUE = 2                   # jobs sent to a technician's queue; the rest of the route waits
# What a minute of technician time is worth to the work still to come. Charging it when
# choosing a technician sends each job to whoever finishes it soonest; charging it when
# deciding whether to take a job at all only pays when the fleet is overloaded (level 2 has
# twice the work it can do), so there it scales with the load: nothing below LOAD_LO of the
# remaining capacity, the full OPP from LOAD_HI up.
OPP = 0.2
LOAD_LO, LOAD_HI = 0.5, 1.0
INS_PASSES = 2                  # relocation passes over the routes
LIGHT_AFTER = 12.0              # seconds into a reply: skip the relocation passes and recalls
FALLBACK_AFTER = 30.0           # ... : send the plain earliest-deadline plan instead
# our own replies echoed in the history ("[Player] ..." or "[Player 0] ...")
PLAYER_LINE = re.compile(r"^\[Player[^\]]*\]")
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
# the message refers to a place ("calling from X", "here at X", "(X)")
SITE_REF = re.compile(r"\b(calling from|here at|we are at|this is .{2,30} again)\b|\(")
USE_LLM = True              # SHA D: the LLM reader reads every new ticket. False = rules only (SHA C).
LLM_PRIMARY = True          # level 4: take the model's reading first, the rules fill its gaps.
# Set from the game id in reset(): the regex lists read the hand-written wording of levels
# 1-3 exactly, so there the model only fills gaps; from level 4 the tickets are paraphrased
# (a bank the lists never saw) and the model reads first. Unknown level: model first.
RULES_LEVELS = re.compile(r"L[123](?!\d)")
LLM_FILL = 0.6              # model confidence needed to fill what the rules left open
LLM_HIGH = 0.85             # ... to overrule the rules on a high-stakes disagreement
LLM_SURE = 0.9              # ... to take a site, or to close a ticket as not a job


class Agent:
    def __init__(self):
        self.rng = random.Random(7)
        self.started = time.monotonic()

    def reset(self, game_id, player_id):
        global LLM_PRIMARY
        self.game_id = game_id
        LLM_PRIMARY = not RULES_LEVELS.search(str(game_id or ""))

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
        P = _parsers(obs)
        jobs = P.dd_parse_jobs(obs)
        techs = P.dd_parse_techs(obs)
        travel = P.dd_travel(obs)
        directory = P.dd_parse_directory(obs)
        msgs = P.dd_parse_messages(obs)
        now, tick, ticks = P.dd_now(obs)
        rules = parse_rules(obs)
        shift_end = rules["shift_end"]
        byid = {j["jid"]: j for j in jobs}
        self._obs_cache = llm_reader.decode(obs) if llm_reader else {}
        # tickets the customer confirmed are a repeat of an earlier one: never dispatched
        same_issue = set(re.findall(r"about (j\d+) \(problem\): \"yes - this is the same issue", obs.lower()))
        # customer sites: a job whose site is unknown is at one of these, never at the depot
        sites = sorted({c["zone"] for c in directory if c["zone"] in travel}) \
            or [z for z in travel if z != "Depot"] or list(travel)

        # Only our own replies count as "asked": the rules text and the board footer both
        # print example asks ("[ask J7 location]", "[ask J4 location]").
        mine = " ".join(l for l in obs.splitlines() if PLAYER_LINE.match(l))
        asked = lambda jid, topic: "[ask %s %s]" % (jid, topic) in mine
        asked_any = set(re.findall(r"\[ask (J\d+) ", mine))

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

        # ---- the LLM reader: once per new ticket, answers carried in the reply notes
        cache, notes = {}, []
        try:
            cache, notes = self._consult(jobs, msgs, directory, techs, sites)
        except Exception as e:
            print("agent: LLM reader skipped: %s: %s" % (type(e).__name__, e), file=sys.stderr)

        # ---- read every real job still waiting for a technician
        for j in jobs:
            if j["status"] != "unassigned" or j["jid"] in repeats:
                continue
            if j["jid"].lower() in same_issue:
                continue
            blob = self._blob(j, msgs)
            zone = j["zone"] or self._site(blob, directory) or hints.get(j["jid"])
            trade = j["skill"] or self._trade(blob)
            prio = j["prio"] or self._priority(blob)
            llm = cache.get(j["jid"])
            if llm:
                verdict = self._combine(j, blob, zone, trade, prio, llm, directory, sites, msgs, asked)
                if verdict is None:
                    closes.append("[ignore %s]" % j["jid"])     # neither reader sees a request
                    continue
                zone, trade, prio, extra, hold = verdict
                asks.extend(extra)
                if hold:
                    continue
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
                # A P3 with an unknown site is left: a probe ties a technician up for 10
                # points, and an ask (5) rarely leaves anything once the drive is paid.
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

        # ---- technicians on the road to lower-priority work, who a late P1 may recall
        recallable = []
        for t in techs:
            cur = byid.get(t["job"])
            if t["activity"] != "travel" or not cur or cur["status"] != "travelling" or t["queue"] \
                    or t["off_shift"] or not t["dest"] or not t["until"] or cur["jid"] in repeats:
                continue
            cblob = self._blob(cur, msgs)
            cprio = cur["prio"] or self._priority(cblob) or 2
            if cprio == 1:
                continue
            back = self._plan(dict(cur, status="unassigned"), cur["zone"] or t["dest"],
                              cur["skill"] or self._trade(cblob) or "electrical", cprio, cblob,
                              directory, sites, rules)
            recallable.append((t["tid"], back, cprio, max(0, t["until"] - now)))
        # each candidate re-plans the whole board: keep the three most worth recalling
        recallable = [(tid, back) for tid, back, _, _ in
                      sorted(recallable, key=lambda r: (-r[2], -r[3]))[:3]]

        # ---- time guard: a cluster pod is slower than a laptop and the reply limit is 45 s
        elapsed = time.monotonic() - self.started
        if elapsed > FALLBACK_AFTER:
            cmds = self._greedy(plans, techs, travel)
            head = ["llm " + " ".join(notes) + " ;"] if notes else []
            return " ".join(head + guards.legal(closes + cmds, jobs, techs, travel, asked_any))
        self.passes = 0 if elapsed > LIGHT_AFTER else INS_PASSES
        if elapsed > LIGHT_AFTER:
            recallable = []

        # ---- full-horizon routes, re-planned from scratch every tick
        self.next_tick = now + rules["tick"]
        for p in plans + [b for _, b in recallable]:
            p["can_ask"] = p["zone"] is None and not asked(p["job"]["jid"], "location")
        recalls = self._insertion(plans, techs, travel, rules, now, recallable)

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
        departs_of = {p["job"]["jid"]: p.get("departs", now) for p in plans}
        planned = {jid for t in techs for jid, _ in t["queue"]}
        ranked = [((1, -1, 0), c) for c in recalls]
        for t in techs:
            want = t["queue"]
            have = current[t["tid"]]
            k = 0
            while k < len(want) and k < len(have) and want[k][0] == have[k]:
                k += 1
            cmds = ["[unassign %s]" % jid for jid in have[k:] if jid in replannable and jid not in planned]
            cmds += ["[assign %s %s %s]" % (t["tid"], jid, zone) for jid, zone in want[k:] if zone]
            if cmds:
                changed = [jid for jid, _ in want[k:]]
                top = min([prio_of.get(jid, 3) for jid in changed] + [3])
                soon = min([departs_of.get(jid, now) for jid in changed] + [shift_end])
                # a dispatch the technician acts on before the next tick is worth its slot now;
                # a second job in a busy queue can wait a tick behind the questions
                key = (top, 0 if soon < now + rules["tick"] else 2, soon)
                ranked += [(key, c) for c in cmds]
            t["queue"] = [jid for jid, _ in want]
        for c in asks + asks_p3:
            jid = c.split()[1]
            ranked.append(((prio_of.get(jid, 3), 1, 0), c))
        ranked += [((8, 0, 0), c) for c in merges] + [((9, 0, 0), c) for c in closes]
        # the reply carries four commands: P1 work first, then P1 questions, P2 work, P2
        # questions, P3 work; merges and ignores earn nothing and wait for a free slot
        ranked.sort(key=lambda x: x[0])
        cmds = [c for _, c in ranked]
        # the fifth slot (guards.FULL_REPLY) is only worth its risk for P1/P2 work the
        # technician acts on before the next tick (a queued second job can wait a tick)
        if len(ranked) >= guards.FULL_REPLY and ranked[guards.FULL_REPLY - 1][0][:2] > (2, 0):
            cmds = cmds[:guards.MAX_COMMANDS]
        # model answers ride along outside the brackets, so a restarted agent still has them
        head = ["llm " + " ".join(notes) + " ;"] if notes else []
        out = guards.legal(cmds, jobs, techs, travel, asked_any)
        # The replay shows commands, not notes: once per shift, when the reader was switched
        # on but got no answer, send a harmless "[llm <ErrorClass>]" in a spare slot. The game
        # rejects it at no cost (the reply keeps its [wait]) and the replay records why.
        failed = [n.split(":", 1)[1] for n in notes if "=x:" in n or "=r:" in n]
        if USE_LLM and failed and not any(v for v in cache.values()) and len(out) < 5 \
                and "[llm " not in mine:
            out.insert(len(out) - 1, "[llm %s]" % re.sub(r"[^A-Za-z0-9_]", "", failed[0])[:40])
        return " ".join(head + out)

    # ------------------------------------------------------------------ the LLM reader
    def _consult(self, jobs, msgs, directory, techs, sites):
        """Ask the model about every ticket it has not read yet (once per ticket), in
        parallel, within the tick budget. Returns ({jid: reading}, notes for this reply)."""
        if not USE_LLM:
            return {}, []
        if llm_reader is None:
            return {}, (["J0=x:import_" + LLM_IMPORT_ERROR] if LLM_IMPORT_ERROR else [])
        if not llm_reader.configured():
            return {}, []
        obs_cache = self._obs_cache
        if not llm_reader.healthy(obs_cache):
            return obs_cache, []
        trades = sorted({s for t in techs for s in t["skills"]})
        todo = []
        for j in jobs:
            if j["status"] != "unassigned" or j["jid"] in obs_cache:
                continue
            m = msgs.get(j["jid"], {})
            blob = self._blob(j, msgs)
            who = self._customer(blob, directory)
            same = [(o["jid"], msgs.get(o["jid"], {}).get("text", "")) for o in jobs
                    if o["jid"] != j["jid"] and o["minute"] <= j["minute"] and who is not None
                    and self._customer(self._blob(o, msgs), directory) == who]
            todo.append((j["jid"], llm_reader.prompt(j["jid"], m.get("sender", j["sender"]), m.get("text", ""),
                                                     directory, trades, sites, same),
                         {jid for jid, _ in same}))
        todo = todo[:max(0, llm_reader.MAX_CALLS - len(obs_cache))]
        got, notes = llm_reader.consult(todo, trades, sites, self.started)
        cache = dict(obs_cache)
        cache.update(got)
        return cache, notes

    def _combine(self, j, blob, zone, trade, prio, llm, directory, sites, msgs, asked):
        """Merge the model's reading with the regex reader's. The rules win on the wording
        they know (levels 1-3); the model fills what they left open (at LLM_FILL), raises a
        possible P1 (at LLM_HIGH), corrects a trade the customer ruled out, takes a site only
        for a place the message refers to, and holds a repeat only it sees. Disagreements are
        never asked about (every question is a fee). With LLM_PRIMARY (for level 4's unknown
        wording) the model's reading is taken first and the rules fill its gaps. What the game
        confirmed is never overruled.
        Returns None (not a request) or (zone, trade, prio, asks, hold)."""
        c, jid, extra, hold = llm["confidence"], j["jid"], [], False
        ask = lambda topic: extra.append("[ask %s %s]" % (jid, topic)) if not asked(jid, topic) else None
        if llm["not_a_job"] and c >= LLM_SURE and prio is None and (trade is None or LLM_PRIMARY):
            return None                           # level 4: a stray trade word in a thank-you note
        # priority: fill a gap at LLM_FILL; overrule only to raise a possible P1 at LLM_HIGH
        if j["prio"] is None and llm["prio"] in (1, 2, 3):
            # Urgency stays rules-first on every level: when a calm/alarm/today phrase matches
            # it is right even on paraphrased text (12 of 13 on a DeepSeek paraphrase sample),
            # while the model reads "no rush, but by end of day" as a P2. The model fills the
            # gaps, which on level 4 are about 40 % of the tickets.
            if prio is None and c >= LLM_FILL:
                prio = llm["prio"]
        # trade: fill a gap; never a trade the customer ruled out; overrule the rules only when
        # they fell back on a ruled-out trade, or when the model reads first (LLM_PRIMARY,
        # level 4: on paraphrased text the lists read 20 of 42 trades, the model 41 of 42)
        if j["skill"] is None and llm["trade"]:
            ruled_out = {t for phrase, t in EXONERATE if phrase in blob}
            if llm["trade"] not in ruled_out:
                if trade is None:
                    if c >= LLM_FILL:
                        trade = llm["trade"]
                elif llm["trade"] != trade and ((trade in ruled_out and c >= LLM_HIGH)
                                                or (LLM_PRIMARY and c >= LLM_FILL)):
                    trade = llm["trade"]
        # site: only fill a gap, only a place the message refers to, never a decoy zone
        text = msgs.get(jid, {}).get("text", "").lower()
        if zone is None and llm["zone"] and c >= LLM_SURE and (LLM_PRIMARY or SITE_REF.search(text)) \
                and llm["zone"] in self._zone_odds(trade, blob, directory, sites):
            zone = llm["zone"]
        # a likely repeat the rules missed: never dispatch it before the customer confirms.
        # The rules catch every follow-up phrasing of levels 1-3, so only when the model
        # reads first (level 4) is its repeat worth a question and a tick.
        if LLM_PRIMARY and llm["repeat_of"] and c >= LLM_HIGH and not asked(jid, "problem"):
            ask("problem")
            hold = True
        return zone, trade, prio, extra, hold

    # ------------------------------------------------------------------ route planner
    def _simulate(self, t, seq, travel, rules, lam):
        """Walk technician t through seq: (objective, [(zone, departs, on_time, mode)]). A job
        whose site is unknown is either probed now ("go": the expected detour) or asked about
        and set off for at the next tick straight to the confirmed site ("ask"): whichever
        adds more."""
        time_, at, total, legs = t["free_at"], t["at"], 0.0, []
        own_end = t["shift_end"] or rules["shift_end"]
        for p in seq:
            zone, drive, delay = self._route(at, p, travel)
            options = [self._leg(p, time_, time_, drive, delay, 0.0, own_end, rules, lam) + (zone, "go")]
            # a P1 cannot spare the tick an answer takes; a P2 (4 h SLA) often can
            if p["zone"] is None and p.get("can_ask") and p["prio"] >= 2:
                start = max(time_, self.next_tick)
                exp_drive = sum(q * travel.get(at, {}).get(z, 30) for z, q in p["cands"].items())
                options.append(self._leg(p, time_, start, exp_drive, 0.0, rules["fee"], own_end, rules, lam)
                               + (zone, "ask"))
            gain, finish, on_time, departs, zone, mode = max(options, key=lambda o: o[0])
            total += gain
            legs.append((zone, departs, on_time, mode))
            time_, at = finish, p["zone"] or zone
        return total, legs

    @staticmethod
    def _leg(p, free, start, drive, delay, fee, own_end, rules, lam):
        """(worth, finish, chance on time, departure) of one job set off for at `start`."""
        duration = DURATION_GUESS[p["trade"]]
        lo, hi = DURATION_SPREAD.get(p["trade"], (duration, duration))
        arrival = start + drive + delay
        finish = arrival + duration
        on_time = sum(arrival + d <= p["deadline"] for d in (lo, duration, hi)) / 3.0
        value = rules["value"].get(p["prio"], 0) * (on_time + (1 - on_time) * rules["late"])
        if finish > rules["hard_stop"]:
            value = 0.0
        overtime = max(0, finish - max(own_end, free))
        worth = value - fee - rules["travel"] * drive - rules["overtime"] * overtime - lam * (finish - free)
        return worth, finish, on_time, start

    def _routes(self, plans, crew, travel, rules, lam):
        """Full-horizon routes: each job goes where it adds the most to the whole plan
        (cheapest insertion, then relocation passes); a job that adds nothing is refused.
        Returns ({tid: [plan, ...]}, total value)."""
        routes = {t["tid"]: [] for t in crew}
        value = {t["tid"]: 0.0 for t in crew}

        def best_slot(p):
            best = None
            for t in crew:
                if p["trade"] not in t["skills"]:
                    continue
                seq = routes[t["tid"]]
                for i in range(len(seq) + 1):
                    v, _ = self._simulate(t, seq[:i] + [p] + seq[i:], travel, rules, lam)
                    if best is None or v - value[t["tid"]] > best[0]:
                        best = (v - value[t["tid"]], t, i, v)
            return best

        order = sorted(plans, key=lambda p: (-rules["value"].get(p["prio"], 0), p["deadline"]))
        for p in order:
            b = best_slot(p)
            if b and b[0] > 0:
                routes[b[1]["tid"]].insert(b[2], p)
                value[b[1]["tid"]] = b[3]
        for _ in range(getattr(self, "passes", INS_PASSES)):
            moved = False
            for p in order:
                home = next((t for t in crew if p in routes[t["tid"]]), None)
                if home is not None:
                    routes[home["tid"]].remove(p)
                    value[home["tid"]], _ = self._simulate(home, routes[home["tid"]], travel, rules, lam)
                b = best_slot(p)
                if b and b[0] > 0:
                    routes[b[1]["tid"]].insert(b[2], p)
                    value[b[1]["tid"]] = b[3]
                    moved = moved or b[1] is not home
                elif home is not None:
                    moved = True
            if not moved:
                break
        return routes, sum(value.values())

    def _greedy(self, plans, techs, travel):
        """The fallback when time is short: earliest deadline first, each job to the idle
        technician with the skill who is closest, one job each."""
        cmds, busy = [], set()
        for p in sorted(plans, key=lambda p: (p["deadline"], p["prio"])):
            if p["zone"] is None or p["job"]["status"] != "unassigned":
                continue
            free = [t for t in techs if p["trade"] in t["skills"] and not t["off_shift"]
                    and not t["queue"] and t["tid"] not in busy and t["at"] in travel]
            if free:
                t = min(free, key=lambda t: travel[t["at"]].get(p["zone"], 99))
                busy.add(t["tid"])
                cmds.append("[assign %s %s %s]" % (t["tid"], p["job"]["jid"], p["zone"]))
        return cmds

    def _insertion(self, plans, techs, travel, rules, now, recallable):
        """Plan the routes; when a job would be refused or late, also try recalling each
        technician who is on the road to lower-priority work (the game sets them down at that
        site at once, idle, and the job goes back into the pool). Keep whichever plan is worth
        more. Returns the unassign commands for the recalls made."""
        lam = self._accept_opp()
        crew = [t for t in techs if not t["off_shift"] and t["at"] in travel]
        routes, total = self._routes(plans, crew, travel, rules, lam)
        recalls = []
        planned = {id(p): (t, k) for t in crew for k, p in enumerate(routes[t["tid"]])}

        def late_or_refused(routes):
            for t in crew:
                _, legs = self._simulate(t, routes[t["tid"]], travel, rules, lam)
                if any(leg[2] < 1 for leg in legs):
                    return True
            return any(id(p) not in planned for p in plans)

        if recallable and late_or_refused(routes):
            best = None
            for tid, back in recallable:
                t = next((c for c in crew if c["tid"] == tid), None)
                if t is None:
                    continue
                there = dict(t, free_at=now, at=t["dest"])   # the game puts them there at once
                kept, _ = self._simulate(there, [back], travel, rules, lam)   # the job as it goes now
                alt_crew = [there if c is t else c for c in crew]
                alt_routes, alt_total = self._routes(plans + [back], alt_crew, travel, rules, lam)
                if alt_routes[tid][:1] == [back]:
                    # never recall just to hand the same job straight back: the game would set
                    # the technician down there at once, a teleport, not a better plan
                    continue
                gain = alt_total - (total + kept)
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, t, there, back, alt_crew, alt_routes)
            if best:
                _, t, there, back, crew, routes = best
                t["free_at"], t["at"] = there["free_at"], there["at"]
                crew = [t if c is there else c for c in crew]
                plans.append(back)
                recalls.append("[unassign %s]" % back["job"]["jid"])
        # commit the head of each route; the rest waits for the next tick's re-plan
        for t in crew:
            seq = routes[t["tid"]]
            _, legs = self._simulate(t, seq, travel, rules, lam)
            sending = True
            for k, (p, (zone, departs, on_time, mode)) in enumerate(zip(seq, legs)):
                p["tech"], p["departs"] = t, departs
                if mode == "ask":
                    sending = False               # asked now; the route resumes next tick
                if sending and k < MAX_QUEUE - len(t["queue"]):
                    t["queue"] = t["queue"] + [(p["job"]["jid"], zone)]
        return recalls

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
            history = [{"jid": jid, "minute": m["minute"], "blob": (m["sender"] + " " + m["text"]).lower()}
                       for jid, m in msgs.items()]
            history = [h for h in history if not any(p in h["blob"] for p in NOT_A_JOB + FOLLOW_UP)]
            if handlers.ambiguous_original(dup, who, topic, history,
                                           lambda b: self._customer(b, directory), self._trade):
                return None, None, named          # the open one may not be the original
            return named[0]["jid"], None, named
        if not named and len(anonymous) == 1 and who is not None:
            zone = next(c["zone"] for c in directory if c["name"] == who)
            return None, (anonymous[0]["jid"], zone), anonymous
        return None, None, named + anonymous

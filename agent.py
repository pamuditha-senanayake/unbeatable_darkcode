"""Reference policy: read the ticket properly before dispatching.

The keyword anchor loses points in three specific ways, and this agent fixes each one
without any model in the loop. It is the "a careful human would do this" baseline:

* **Negated urgency.** Half the P3 tickets are written "no need to hurry, it is not
  critical" - a substring match on "hurry"/"critical" reads them as emergencies. So the
  calm phrasings are checked FIRST and only then the alarm words.
* **The decoy trade.** Vague tickets describe a symptom in the vocabulary of the wrong
  trade, and usually say out loud that the obvious trade has been ruled out ("the
  electrician we called said it is not his thing", "power is fine", "water side looks
  fine"). Those exonerations remove a trade from the running, and a few cause-level
  rules catch the rest - several unrelated systems failing at one instant is an
  electrical supply fault, not a coincidence.
* **The decoy place.** "Our driver is coming from Kista" names a zone that is not the
  site. So a zone is only believed when it comes from the customer directory (name or
  nickname) or from the game itself; a bare zone name in the prose is ignored and the
  site is asked for instead.

It then dispatches by SLA deadline rather than by guessed priority, and refuses work it
cannot finish before the shift ends, because overtime at 1/min outruns a P3's 10 points.
"""
import random
import re

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


class Agent:
    def __init__(self):
        self.rng = random.Random(7)

    def reset(self, game_id, player_id):
        self.game_id = game_id

    def __call__(self, observation):
        # Never raise: a raised reply is a fault, and four faults lose the shift.
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

        for t in techs:
            t["free_at"] = max(now, t["until"] or now) + 45 * len(t["queue"])
            t["at"] = t["dest"] or t["zone"]

        # Only our own replies count as "asked": the rules text and the board footer both
        # print example asks ("[ask J7 location]", "[ask J4 location]").
        mine = " ".join(l for l in obs.splitlines() if l.startswith("[Player]"))
        asked = lambda jid, topic: "[ask %s %s]" % (jid, topic) in mine

        closes, merges, asks, plans, probes = [], [], [], [], []
        for j in jobs:
            if j["status"] != "unassigned":
                continue
            blob = self._blob(j, msgs)

            if any(p in blob for p in NOT_A_JOB):
                closes.append("[ignore %s]" % j["jid"])
                continue
            if any(p in blob for p in FOLLOW_UP):
                orig = self._original(j, blob, jobs, msgs, directory, obs)
                if orig:
                    merges.append("[merge %s %s]" % (j["jid"], orig))
                    continue
                # A repeat whose original we cannot name: confirm it before sending anyone.
                # Once the customer says it is the same issue, leave it; the original is handled.
                if not asked(j["jid"], "problem"):
                    asks.append("[ask %s problem]" % j["jid"])
                    continue
                if re.search(r"about %s \(problem\): \"yes - this is the same issue" % j["jid"], obs.lower()):
                    continue
                if j["skill"] is None:
                    continue                      # answer still pending

            zone = j["zone"] or self._site(blob, directory)
            trade = j["skill"] or self._trade(blob)
            prio = j["prio"] or self._priority(blob)
            # Ask about whatever the text does not tell us, all in the same tick. Once an
            # answer is in, the game shows it on the row; if it is still pending, hold the job.
            if zone is None and trade and prio in (1, 2) and not asked(j["jid"], "location"):
                # P1/P2 with an unknown site: try to probe it (below). A P3 can afford the
                # tick an ask takes, and probing it burns technician time for 10 points.
                sla = rules["sla"].get(prio)
                probes.append({"job": j, "trade": trade, "prio": prio,
                               "deadline": shift_end if sla is None else min(j["minute"] + sla, shift_end)})
                continue
            # An ask only pays when the job is worth more than the fees plus the drive it will
            # cost. For a P3 (10) with an unknown site, 5 + an average drive usually is not.
            need = [t for v, t in ((zone, "location"), (trade, "problem"), (prio, "urgency"))
                    if v is None and not asked(j["jid"], t)]
            if need and prio is not None and travel:
                crew = [t for t in techs if not t["off_shift"] and (trade is None or trade in t["skills"])]
                sites = [z for z in travel if z != "Depot"] or list(travel)
                drive = (sum(min(travel.get(t["at"], {}).get(z, 30) for t in crew) for z in sites) / len(sites)
                         if zone is None and crew else 0)
                if rules["value"].get(prio, 0) - rules["fee"] * len(need) - rules["travel"] * drive <= 0:
                    continue
            for value, topic in ((zone, "location"), (trade, "problem"), (prio, "urgency")):
                if value is None:
                    if not asked(j["jid"], topic):
                        asks.append("[ask %s %s]" % (j["jid"], topic))
            # hold the job while an answer is pending; after the answer, fall back to defaults
            if zone is None or (trade is None and not asked(j["jid"], "problem")) \
                    or (prio is None and not asked(j["jid"], "urgency")):
                continue
            trade = trade or "electrical"
            prio = prio or 2
            sla = rules["sla"].get(prio)
            deadline = shift_end if sla is None else min(j["minute"] + sla, shift_end)
            plans.append({"job": j, "zone": zone, "trade": trade, "prio": prio, "deadline": deadline})

        # Re-plan every tick: jobs still waiting in a queue (nobody on the road to them yet)
        # go back into the pool, so a new P1 can jump ahead of queued P3 work.
        current = {t["tid"]: list(t["queue"]) for t in techs}
        replannable = set()
        for j in jobs:
            if j["status"] != "queued":
                continue
            blob = self._blob(j, msgs)
            sent = re.findall(r"\[assign T\d+ %s (\S+)\]" % j["jid"], mine)
            zone = j["zone"] or (sent[-1] if sent else None)
            if zone is None:
                continue
            trade = j["skill"] or self._trade(blob) or "electrical"
            prio = j["prio"] or self._priority(blob) or 2
            sla = rules["sla"].get(prio)
            deadline = shift_end if sla is None else min(j["minute"] + sla, shift_end)
            plans.append({"job": j, "zone": zone, "trade": trade, "prio": prio, "deadline": deadline})
            replannable.add(j["jid"])
        for t in techs:
            # a queued job we could not re-plan (unreadable row) stays where it is, in front
            t["queue"] = [(jid, None) for jid in t["queue"] if jid not in replannable]
            t["free_at"] = max(now, t["until"] or now)
            if t["activity"] == "travel":         # the job they are driving to still has to be done
                cur = next((j for j in jobs if j["jid"] == t["job"]), None)
                t["free_at"] += DURATION_GUESS.get(cur and (cur["skill"] or self._trade(self._blob(cur, msgs))), 45)

        # A P1/P2 whose site is unknown: rather than ask (a fee and a lost tick), send a
        # technician with the right skill to the zone they are standing in (or about to be
        # in). A wrong site costs them 15 minutes, reveals the true zone and they drive
        # straight on; the drive is the one they would have made anyway. P1 probes are
        # planned first (a queued job must not take the technician a P1 needs to find its
        # site); P2 probes take their turn in deadline order, so they cannot jump a P1.
        # With no suitable technician free soon, ask.
        sites = [z for z in travel if z != "Depot"] or list(travel)
        for p in probes:
            p["probe"] = True

        # P1 probes, then earliest deadline first, and a P1 ahead of a P3 at the same deadline
        plans = sorted(plans + probes, key=lambda p: (not (p.get("probe") and p["prio"] == 1), p["deadline"], p["prio"]))
        assigns = []
        for p in plans:
            if p.get("probe"):
                # One who finishes within 15 min still beats an ask, whose answer only
                # comes at the next tick.
                idle = [t for t in techs if p["trade"] in t["skills"] and not t["off_shift"] and not t["queue"]
                        and t["at"] in travel and t["free_at"] <= now + 15
                        and t["free_at"] + 15 + 45 + DURATION_GUESS.get(p["trade"], 60) <= (t["shift_end"] or shift_end)]
                if not idle or not travel:
                    asks.append("[ask %s location]" % p["job"]["jid"])
                    continue
                t = min(idle, key=lambda t: (t["free_at"], sum(travel.get(t["at"], {}).get(z, 30) for z in sites)))
                t["queue"] = [(p["job"]["jid"], t["at"])]
                t["free_at"] += 60 + DURATION_GUESS.get(p["trade"], 60)
                continue
            # Pick the technician with the best expected net: value earned (half if past the
            # SLA, nothing past the 19:00 hard stop) minus travel and their own overtime.
            # A job no technician can do at a profit is left alone, whatever its priority.
            best, best_arrival, best_net = None, None, 0.0
            duration = DURATION_GUESS[p["trade"]]
            for t in techs:
                if p["trade"] not in t["skills"] or t["off_shift"] or len(t["queue"]) >= MAX_QUEUE:
                    continue
                drive = travel.get(t["at"], {}).get(p["zone"], 30)
                arrival = t["free_at"] + drive
                finish = arrival + duration
                own_end = t["shift_end"] or shift_end
                # expected value over the likely durations: a finish a few minutes past the SLA
                # is a coin flip, not a certainty either way
                lo, hi = DURATION_SPREAD.get(p["trade"], (duration, duration))
                outcomes = [arrival + d <= p["deadline"] for d in (lo, duration, hi)]
                on_time = sum(outcomes) / 3.0
                value = rules["value"].get(p["prio"], 0) * (on_time + (1 - on_time) * rules["late"])
                if finish > rules["hard_stop"]:
                    value = 0.0
                overtime = max(0, finish - max(own_end, t["free_at"]))
                net = value - rules["travel"] * drive - rules["overtime"] * overtime
                if net > best_net:
                    best, best_arrival, best_net = t, arrival, net
            if best is None:
                continue
            best["queue"] = best["queue"] + [(p["job"]["jid"], p["zone"])]
            best["free_at"] = best_arrival + DURATION_GUESS[p["trade"]]
            best["at"] = p["zone"]

        # Send only the differences. Re-assigning a queued job moves it to the back of the
        # named queue, so everything after the first change in a queue is re-sent in order.
        planned = {jid for t in techs for jid, _ in t["queue"]}
        for t in techs:
            want = t["queue"]
            have = current[t["tid"]]
            k = 0
            while k < len(want) and k < len(have) and want[k][0] == have[k]:
                k += 1
            for jid in have[k:]:
                if jid in replannable and jid not in planned:
                    assigns.append("[unassign %s]" % jid)
            assigns += ["[assign %s %s %s]" % (t["tid"], jid, zone) for jid, zone in want[k:] if zone]
            t["queue"] = [jid for jid, _ in want]

        cmds = closes + merges + assigns + asks
        return " ".join(cmds[:4] + ["[wait]"])

    def _blob(self, j, msgs):
        """Sender and text, lower-cased. The job row cuts the sender at 28 characters, so
        take it from the message line where it is printed in full."""
        m = msgs.get(j["jid"], {})
        return (m.get("sender", j["sender"]) + " " + m.get("text", "")).lower()

    def _original(self, dup, blob, jobs, msgs, directory, obs=""):
        """The earlier open job this repeat is about: same customer, and when the customer
        has several, the one whose trade matches what the follow-up says it is about."""
        who = self._customer(blob, directory)
        if who is None:
            return None
        topic = next((trade for words, trade in TOPIC if any(w in blob for w in words)), None)
        found = []
        for j in jobs:
            if j["jid"] == dup["jid"] or j["minute"] > dup["minute"]:
                continue
            if "[merge %s %s] wrong" % (dup["jid"], j["jid"]) in obs:
                continue                          # the game already said no
            other = self._blob(j, msgs)
            # never merge into a non-request (we ignore it) or into another repeat
            if any(p in other for p in NOT_A_JOB) or any(p in other for p in FOLLOW_UP):
                continue
            if self._customer(other, directory) == who:
                found.append((j, other))
        if topic:
            for j, other in found:
                if (j["skill"] or self._trade(other)) == topic:
                    return j["jid"]
        return found[0][0]["jid"] if found else None

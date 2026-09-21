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
SLA_MINUTES = {1: 120, 2: 240, 3: None}
DURATION_GUESS = {"HVAC": 60, "refrigeration": 60, "electrical": 45, "IT": 40, "lift": 75,
                  "plumbing": 50}
MAX_QUEUE = 2


class Agent:
    def __init__(self):
        self.rng = random.Random(7)

    def reset(self, game_id, player_id):
        self.game_id = game_id

    def __call__(self, observation):
        # Never raise: a raised reply is a fault, and four faults lose the shift.
        try:
            if detect_game(observation) != "dispatchdesk":
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
        shift_end = ticks * 30

        for t in techs:
            t["free_at"] = max(now, t["until"] or now) + 45 * len(t["queue"])
            t["at"] = t["dest"] or t["zone"]

        # Only our own replies count as "asked": the rules text and the board footer both
        # print example asks ("[ask J7 location]", "[ask J4 location]").
        mine = " ".join(l for l in obs.splitlines() if l.startswith("[Player]"))
        asked = lambda jid, topic: "[ask %s %s]" % (jid, topic) in mine

        closes, merges, asks, plans = [], [], [], []
        for j in jobs:
            if j["status"] != "unassigned":
                continue
            blob = self._blob(j, msgs)

            if any(p in blob for p in NOT_A_JOB):
                closes.append("[ignore %s]" % j["jid"])
                continue
            if any(p in blob for p in FOLLOW_UP):
                orig = self._original(j, blob, jobs, msgs, directory)
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
            sla = SLA_MINUTES[prio]
            deadline = shift_end if sla is None else min(j["minute"] + sla, shift_end)
            plans.append({"job": j, "zone": zone, "trade": trade, "prio": prio, "deadline": deadline})

        # earliest deadline first, and a P1 ahead of a P3 that shares its deadline
        plans.sort(key=lambda p: (p["deadline"], p["prio"]))
        assigns = []
        for p in plans:
            best, best_arrival = None, None
            for t in techs:
                if p["trade"] not in t["skills"] or t["off_shift"] or len(t["queue"]) >= MAX_QUEUE:
                    continue
                arrival = t["free_at"] + travel.get(t["at"], {}).get(p["zone"], 30)
                if best is None or arrival < best_arrival:
                    best, best_arrival = t, arrival
            if best is None:
                continue
            # would only earn overtime: leave it rather than pay 1/min to finish it late
            if best_arrival + DURATION_GUESS[p["trade"]] > (best["shift_end"] or shift_end) + 30 and p["prio"] == 3:
                continue
            assigns.append("[assign %s %s %s]" % (best["tid"], p["job"]["jid"], p["zone"]))
            best["queue"] = best["queue"] + [p["job"]["jid"]]
            best["free_at"] = best_arrival + DURATION_GUESS[p["trade"]]
            best["at"] = p["zone"]

        cmds = closes + merges + assigns + asks
        return " ".join(cmds[:4] + ["[wait]"])

    def _blob(self, j, msgs):
        """Sender and text, lower-cased. The job row cuts the sender at 28 characters, so
        take it from the message line where it is printed in full."""
        m = msgs.get(j["jid"], {})
        return (m.get("sender", j["sender"]) + " " + m.get("text", "")).lower()

    def _original(self, dup, blob, jobs, msgs, directory):
        who = self._customer(blob, directory)
        if who is None:
            return None
        for j in jobs:
            if j["jid"] == dup["jid"] or j["minute"] > dup["minute"]:
                continue
            other = self._blob(j, msgs)
            if self._customer(other, directory) == who:
                return j["jid"]
        return None

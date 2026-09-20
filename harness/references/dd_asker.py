"""Reference policy: buy the facts instead of guessing them.

A deliberately one-eyed strategy, in the field to answer one question for the teams: is
the information worth its price? Every new ticket gets asked for its site and its trade
(5 each, answer next tick) and nothing is dispatched until the board shows both
confirmed. Dispatches are therefore almost never wrong - but the fees are real money and
every answer costs a tick of technician time, so the SLA clock runs while it waits.

Compare it against dd_careful, which pays for almost nothing and reads instead.
"""
import random

from common import (dd_now, dd_parse_jobs, dd_parse_messages, dd_parse_techs, dd_travel,
                    detect_game)

NOT_A_JOB = ["invoice", "wrong number", "sandwiches", "no action needed", "nothing broken",
             "no fault to report", "technician visited", "for your records", "fixed it yesterday",
             "expires next month", "closing the loop", "sorted it out", "email a quote"]
DURATION_GUESS = 55


class Agent:
    def __init__(self):
        self.rng = random.Random(11)

    def reset(self, game_id, player_id):
        self.game_id = game_id

    def __call__(self, observation):
        if detect_game(observation) != "dispatchdesk":
            return "[wait]"
        return self._shift(observation)

    def _shift(self, obs):
        jobs = dd_parse_jobs(obs)
        techs = dd_parse_techs(obs)
        travel = dd_travel(obs)
        msgs = dd_parse_messages(obs)
        now, tick, ticks = dd_now(obs)

        for t in techs:
            t["free_at"] = max(now, t["until"] or now) + 45 * len(t["queue"])
            t["at"] = t["dest"] or t["zone"]

        asks, closes, ready = [], [], []
        for j in jobs:
            if j["status"] != "unassigned":
                continue
            blob = (j["sender"] + " " + msgs.get(j["jid"], {}).get("text", "")).lower()
            if any(p in blob for p in NOT_A_JOB):
                closes.append("[ignore %s]" % j["jid"])
                continue
            # the answer to a question already asked is on its way; do not pay twice
            if j["zone"] is None:
                if "[ask %s location]" % j["jid"] not in obs:
                    asks.append("[ask %s location]" % j["jid"])
                continue
            if j["skill"] is None:
                if "[ask %s problem]" % j["jid"] not in obs:
                    asks.append("[ask %s problem]" % j["jid"])
                continue
            ready.append(j)

        # everything here has a confirmed site and trade: send the nearest able technician
        ready.sort(key=lambda j: j["minute"])
        assigns = []
        for j in ready:
            best, best_arrival = None, None
            for t in techs:
                if j["skill"] not in t["skills"] or t["off_shift"]:
                    continue
                arrival = t["free_at"] + travel.get(t["at"], {}).get(j["zone"], 30)
                if best is None or arrival < best_arrival:
                    best, best_arrival = t, arrival
            if best is None:
                continue
            assigns.append("[assign %s %s %s]" % (best["tid"], j["jid"], j["zone"]))
            best["free_at"] = best_arrival + DURATION_GUESS
            best["at"] = j["zone"]

        cmds = closes + assigns + asks
        return " ".join(cmds[:4] + ["[wait]"])

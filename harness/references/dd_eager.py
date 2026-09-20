"""Reference policy: dispatch everything immediately, think about nothing.

In the field as the "busy dispatcher" failure mode, and it is the one students write
first. It never asks, never merges, never ignores, guesses the trade from the first
keyword it sees and the site from any zone name in the prose - which is how the decoy
place names ("our driver is coming from Kista") collect their victims. Whoever is
nearest gets the job, queue depth be damned, so the last tickets of the day pile onto
one technician and finish in overtime.

Scores above random because work does get done; scores below anything that reads,
because a third of the visits go to the wrong place or the wrong trade.
"""
import random

from common import dd_parse_jobs, dd_parse_messages, dd_parse_techs, dd_travel, detect_game

FIRST_WORD = [
    ("lift", "lift"), ("elevator", "lift"), ("hoist", "lift"),
    ("fridge", "refrigeration"), ("cold", "refrigeration"), ("freezer", "refrigeration"),
    ("water", "plumbing"), ("pipe", "plumbing"), ("drain", "plumbing"), ("leak", "plumbing"),
    ("wifi", "IT"), ("network", "IT"), ("server", "IT"), ("screen", "IT"),
    ("air con", "HVAC"), ("ac ", "HVAC"), ("ventilation", "HVAC"), ("temperature", "HVAC"),
    ("power", "electrical"), ("lights", "electrical"), ("breaker", "electrical"),
]


class Agent:
    def __init__(self):
        self.rng = random.Random(13)

    def reset(self, game_id, player_id):
        self.game_id = game_id

    def __call__(self, observation):
        if detect_game(observation) != "dispatchdesk":
            return "[wait]"
        jobs = dd_parse_jobs(observation)
        techs = dd_parse_techs(observation)
        travel = dd_travel(observation)
        msgs = dd_parse_messages(observation)
        zones = list(travel)

        cmds = []
        for j in jobs:
            if j["status"] != "unassigned" or len(cmds) >= 4:
                continue
            blob = (j["sender"] + " " + msgs.get(j["jid"], {}).get("text", "")).lower()
            trade = j["skill"] or next((t for w, t in FIRST_WORD if w in blob), "electrical")
            # any zone name in the message will do, decoy or not
            zone = j["zone"] or next((z for z in zones if z != "Depot" and z.lower() in blob), None)
            if zone is None:
                continue
            able = [t for t in techs if trade in t["skills"]] or techs
            best = min(able, key=lambda t: travel.get(t["dest"] or t["zone"], {}).get(zone, 30))
            cmds.append("[assign %s %s %s]" % (best["tid"], j["jid"], zone))
        return " ".join(cmds[:4] + ["[wait]"])

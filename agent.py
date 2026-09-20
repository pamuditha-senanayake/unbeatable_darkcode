"""Your agent. This file is your submission.

The competition calls your agent once per turn, 18 times per shift. It hands you the whole
game so far as one string and expects your commands back as one string.

    class Agent:
        def __call__(self, observation: str) -> str: ...

Nothing else is required. `reset` is optional and is called before every shift.

Three rules that cost teams whole episodes in every rehearsal we ran:

    1. End every reply with [wait]. A reply where every command is rejected is an invalid
       move, and two invalid moves in a row lose the shift. [wait] is always accepted.
    2. Answer within 45 seconds. A late reply, or one that raises, is played as [wait] and
       counts as a fault. The fourth fault in one shift loses it.
    3. Do not rely on what you stored in `self`. After a fault your agent is restarted and
       every attribute is gone. Anything you must not lose, write into your reply text
       outside the brackets: the game ignores it and the next observation contains it.

Check yourself:      codefest validate
Watch a shift:       codefest play
Compare:             codefest compare

The rules of the game, every command and what everything costs, are in the rules sheet you
were given. Read it before you write any parsing.
"""
from common import (dd_now, dd_parse_directory, dd_parse_jobs, dd_parse_messages,
                    dd_parse_techs, dd_travel)

MAX_COMMANDS = 5


class Agent:
    def __init__(self):
        """Once, when your agent is created. Load tables, build a client, nothing slow."""
        self.notes = {}

    def reset(self, game_id: str, player_id: int) -> None:
        """Before every shift. Clear anything you carry between turns."""
        self.notes = {}

    def __call__(self, observation: str) -> str:
        try:
            state = self.parse(observation)
            commands = self.decide(state)
        except Exception:
            # Never let an exception out. A raised reply is a fault, and four faults lose
            # the shift. A bad tick is survivable; a lost shift is not.
            commands = []
        return self.reply(commands)

    # ------------------------------------------------------------------ 1. parse

    def parse(self, observation: str) -> dict:
        """Turn the text into data.

        Every one of these helpers is in `common`, which is installed with the harness and
        copied into harness/references/ so you can read it. They read the LAST board in the
        history, except dd_parse_messages, which collects every customer message ever shown
        (a message is printed in full only on the tick it arrives).
        """
        now, tick, ticks = dd_now(observation)
        return {
            "now": now,                                   # minutes after 08:00
            "tick": tick,
            "ticks": ticks,
            "jobs": dd_parse_jobs(observation),           # jid, sender, zone, skill, prio, status
            "techs": dd_parse_techs(observation),         # tid, skills, zone, activity, queue
            "messages": dd_parse_messages(observation),   # jid -> the customer's own words
            "travel": dd_travel(observation),             # travel["Kista"]["Solna"] == minutes
            "directory": dd_parse_directory(observation), # name, zone, aliases
        }

    # ------------------------------------------------------------------ 2. decide

    def decide(self, state: dict) -> list:
        """Return the commands you want to send this tick, as strings.

        This is the whole competition. What is here now does nothing and scores 0.000, which
        is a legal submission and a starting point.

        Where the points are, from the rules sheet:

          * There is about twice as much work as the day has room for. Deciding what NOT to
            do is worth more than doing everything nearest-first.
          * A P1 is worth 100 and a P3 is worth 10, and they cost the same drive. Reading
            urgency correctly is the single most valuable inference.
          * The priority is never stated. "200 vaccine doses in there" is a P1. "no need to
            hurry, it is not critical" is a P3, even though it says critical.
          * The site is often missing or wrong. A place named in the prose may be a decoy.
          * Some messages repeat a job already open. Merge them or you send two technicians.
          * Some messages are not service requests. Ignore those.

        harness/references/dd_careful.py does all of that with no model, and scores about
        0.35 on level 2 (0.50 on level 1; means over many seeds, a single shift varies a lot).
        Read it. Beating it means scheduling better, not reading better.
        """
        commands = []

        for job in state["jobs"]:
            if job["status"] != "unassigned":
                continue
            message = state["messages"].get(job["jid"], {}).get("text", "")
            # TODO: decide what this ticket is (priority, trade, site) and what to do about
            # it. `message` is the customer's own words. `job["zone"]`, `job["skill"]` and
            # `job["prio"]` are None until the game has confirmed them.
            _ = message

        return commands

    # ------------------------------------------------------------------ 3. reply

    def reply(self, commands: list) -> str:
        """At most five commands, and always a trailing [wait]."""
        return " ".join(list(commands)[:MAX_COMMANDS - 1] + ["[wait]"])

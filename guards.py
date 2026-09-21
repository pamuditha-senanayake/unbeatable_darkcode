"""Guardrails: the last filter on every reply.

Whatever the layers above decided, a command reaches the game only if it is legal on the
current board. Every reply ends in [wait] with at most four other commands, and never:

* refers to a job that is not open on the board (closed, merged or finished jobs are gone),
* assigns to an off-shift or unknown technician, or to a zone that is not on the map,
* assigns the same job twice, or a job already being worked on,
* merges a job into itself or into a later job,
* unassigns or ignores a job that is in progress, or asks an unknown question,
* asks about a job more than once in a shift, or sends a job whose row shows its site
  to a different zone.
"""
import re

CMD = re.compile(r"\[([^\[\]]*)\]")
TOPICS = ("location", "problem", "urgency")
MAX_COMMANDS = 4


def legal(cmds, jobs, techs, zones, asked_before=()):
    """Keep the commands that are legal on this board, in order, at most four, then [wait].

    `jobs` are the open-job rows of the last board, `techs` its technician rows (both as
    parsed by common.dd_parse_jobs / dd_parse_techs), `zones` the zones of the travel table.
    Returns the list of kept commands (with "[wait]" last).
    """
    byid = {j["jid"]: j for j in jobs}
    tech = {t["tid"]: t for t in techs}
    zones = set(zones)
    kept, seen, assigned, asked = [], set(), set(), set(asked_before)
    for raw in cmds:
        m = CMD.fullmatch(raw.strip())
        if not m:
            continue
        parts = m.group(1).split()
        if not parts or raw in seen:
            continue
        verb, args = parts[0].lower(), parts[1:]
        ok = False
        if verb == "assign" and len(args) in (2, 3):
            t, j = tech.get(args[0]), byid.get(args[1])
            zone_ok = (len(args) == 3 and args[2] in zones) or (len(args) == 2 and j is not None and j["zone"])
            if j is not None and j["zone"] and len(args) == 3 and args[2] != j["zone"]:
                zone_ok = False                   # the row shows the site: never another zone
            ok = (t is not None and not t["off_shift"] and j is not None and zone_ok
                  and j["status"] != "in_progress" and args[1] not in assigned)
            if ok:
                assigned.add(args[1])
        elif verb == "unassign" and len(args) == 1:
            j = byid.get(args[0])
            ok = j is not None and j["status"] in ("queued", "travelling")
        elif verb == "ask" and len(args) == 2:
            ok = args[0] in byid and args[1] in TOPICS and args[0] not in asked
            if ok:
                asked.add(args[0])                # one question per job per shift
        elif verb == "merge" and len(args) == 2:
            a, b = byid.get(args[0]), byid.get(args[1])
            ok = (a is not None and b is not None and args[0] != args[1]
                  and b["minute"] <= a["minute"] and a["status"] != "in_progress")
        elif verb == "ignore" and len(args) == 1:
            j = byid.get(args[0])
            ok = j is not None and j["status"] != "in_progress"
        if ok:
            kept.append(raw)
            seen.add(raw)
        if len(kept) == MAX_COMMANDS:
            break
    return kept + ["[wait]"]

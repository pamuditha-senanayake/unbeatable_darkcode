"""Observation parsers shared by the anchor agents.

Observations are the FULL game history for the player (TextArena's default
LLMObservationWrapper), so parsers always take the *last* board/pile block.
Both TextArena and the built-in fallback games are handled.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def detect_game(obs: str) -> str:
    head = obs[:600].lower()
    if "nordserv" in head or "you are the dispatcher" in head:
        return "dispatchdesk"
    if "connect four" in head or "connect 4" in head:
        return "connectfour"
    if "nim" in head and "pile" in head:
        return "nim"
    if "mastermind" in head or "secret code" in head:
        return "mastermind"
    if "tic tac toe" in head or "tic-tac-toe" in head:
        return "tictactoe"
    return "unknown"


# ---------------------------------------------------------------- Connect Four
def parse_c4_board(obs: str) -> Optional[List[List[str]]]:
    """Return the latest board as rows of '.', 'X', 'O' (row 0 = top)."""
    # Board block: header of column numbers, dashes, then rows of tokens.
    blocks = re.findall(r"((?:\d+ )+\d+)\n-+\n((?:[.XO](?: [.XO])*\n?)+)", obs)
    if not blocks:
        return None
    rows_txt = blocks[-1][1].strip().splitlines()
    return [r.split() for r in rows_txt]


def c4_legal_columns(board: List[List[str]]) -> List[int]:
    return [c for c in range(len(board[0])) if board[0][c] == "."]


def c4_drop(board: List[List[str]], col: int, sym: str) -> Optional[Tuple[int, int]]:
    for r in range(len(board) - 1, -1, -1):
        if board[r][col] == ".":
            board[r][col] = sym
            return r, col
    return None


def c4_wins_at(board: List[List[str]], r: int, c: int) -> bool:
    sym = board[r][c]
    R, C = len(board), len(board[0])
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        n = 1
        for s in (1, -1):
            rr, cc = r + dr * s, c + dc * s
            while 0 <= rr < R and 0 <= cc < C and board[rr][cc] == sym:
                n += 1
                rr += dr * s
                cc += dc * s
        if n >= 4:
            return True
    return False


def c4_my_symbol(obs: str) -> str:
    m = re.search(r"Player (\d) in Connect Four", obs)
    if m:
        return "X" if m.group(1) == "0" else "O"
    m = re.search(r"symbol:?\s*'?([XO])", obs)
    return m.group(1) if m else "X"


# ------------------------------------------------------------------------ Nim
def parse_nim_piles(obs: str) -> Optional[List[int]]:
    blocks = re.findall(r"((?:\s*pile \d+: \d+\n?)+)", obs)
    if not blocks:
        return None
    return [int(x) for x in re.findall(r"pile \d+: (\d+)", blocks[-1])]


# ----------------------------------------------------------------- Mastermind
def parse_mastermind_params(obs: str) -> Tuple[int, int, bool]:
    m = re.search(r"code of (\d+) numbers between 1 and (\d+)", obs)
    if not m:
        m = re.search(r"(\d+)[- ]digit code.*?between 1 and (\d+)", obs, re.S)
    length = int(m.group(1)) if m else 4
    maxn = int(m.group(2)) if m else 6
    dup = "repeat" in obs[:800].lower() and "no duplicates" not in obs[:800].lower() and "contains no" not in obs[:800].lower()
    return length, maxn, dup


def parse_mastermind_history(obs: str) -> List[Tuple[List[int], int, int]]:
    """[(guess, black, white), ...] from feedback lines in either backend's wording."""
    hist = []
    for g, b, w in re.findall(r"[Gg]uess:?\s*\[?([\d ]+?)\]?[:,]?\s*(\d+) black peg\(?s?\)?,?\s*(\d+) white", obs):
        hist.append(([int(x) for x in g.split()], int(b), int(w)))
    return hist


def mastermind_feedback(secret: List[int], guess: List[int]) -> Tuple[int, int]:
    s, g = list(secret), list(guess)
    black = sum(1 for i in range(len(s)) if s[i] == g[i])
    for i in range(len(s)):
        if s[i] == g[i]:
            s[i] = g[i] = None
    white = 0
    for x in g:
        if x is not None and x in s:
            white += 1
            s[s.index(x)] = None
    return black, white


# ---------------------------------------------------------------- DispatchDesk
# The board is printed by arena/games/dispatchdesk/env.py (`_board`, `_job_row`, `_tech_row`)
# and the episode header by `_prompt`. These parsers read the LAST board in the history,
# except dd_parse_messages: a customer message is printed in full only on the tick it
# arrives, so it has to be collected from the whole history.

_DD_BOARD_HEAD = re.compile(r"=== (\d{2}):(\d{2}) \| tick (\d+)/(\d+) \|")
_DD_JOB = re.compile(r"^ {2}(J\d+)\s+(\d{2}:\d{2})\s+from (.+?)\s+zone (\S+)\s+skill (\S+)\s+prio (\S+)\s+(\S.*?)\s*$")
_DD_TECH = re.compile(r"^ {2}(T\d+) (\S+)\s+(\S+)\s+(at|->) (\S+)\s+(.*?)\s+queue: (.*)$")
_DD_MSG = re.compile(r'^ {2}(J\d+) (\d{2}:\d{2}) from (.+?): "(.*)"\s*$')
# the episode header roster, which carries each technician's shift end
_DD_ROSTER = re.compile(r"^ {2}(T\d+) \S+\s+\S+\s+starts at (\S+)\s+shift ends (\d{2}:\d{2})")
_DD_OFF_SHIFT = " OFF SHIFT (overtime)"


def _dd_minutes(hhmm: str) -> int:
    """'07:38' -> minutes relative to 08:00 (negative before the shift starts)."""
    h, m = hhmm.split(":")
    return (int(h) - 8) * 60 + int(m)


def _dd_last_board(obs: str) -> str:
    """Everything from the last board header on: the current state of the shift."""
    starts = [m.start() for m in _DD_BOARD_HEAD.finditer(obs)]
    return obs[starts[-1]:] if starts else ""


def _dd_rows(board: str, header: str) -> List[str]:
    """The indented rows directly below `header` (stops at the next unindented line)."""
    rows, inside = [], False
    for line in board.splitlines():
        if not inside:
            inside = line.startswith(header)
        elif line.startswith("  "):
            rows.append(line)
        else:
            break
    return rows


def dd_now(obs: str) -> Tuple[int, int, int]:
    """(minutes after 08:00, current tick, total ticks) from the last board header."""
    ms = list(_DD_BOARD_HEAD.finditer(obs))
    if not ms:
        return 0, 1, 18
    h, m, tick, ticks = ms[-1].groups()
    return (int(h) - 8) * 60 + int(m), int(tick), int(ticks)


def dd_travel(obs: str) -> Dict[str, Dict[str, int]]:
    """Travel-time matrix from the header table: dd_travel(obs)['Kista']['Solna'] == 20."""
    m = re.search(r"ZONES and travel minutes:\n(.*?)\n\s*\n", obs, re.S)
    if not m:
        return {}
    lines = [l for l in m.group(1).splitlines() if l.strip()]
    if not lines:
        return {}
    zones = lines[0].split()
    matrix: Dict[str, Dict[str, int]] = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) == len(zones) + 1 and all(p.lstrip("-").isdigit() for p in parts[1:]):
            matrix[parts[0]] = {z: int(v) for z, v in zip(zones, parts[1:])}
    return matrix


def dd_parse_directory(obs: str) -> List[Dict[str, Any]]:
    """The customer directory: [{'name', 'zone', 'aliases': [...], 'tier': str|None}].

    `tier` is only shown in the mystery variant.
    """
    m = re.search(r"CUSTOMER DIRECTORY[^\n]*\n(.*?)\n\s*\n", obs, re.S)
    if not m:
        return []
    zones = set(dd_travel(obs))
    out = []
    for line in m.group(1).splitlines():
        chunks = re.split(r"\s{2,}", line.strip())
        if len(chunks) < 2:
            continue
        name, zone, rest = chunks[0], chunks[1], " ".join(chunks[2:])
        if zones and zone not in zones:
            continue
        tier = None
        if " | " in rest:
            rest, tier = [x.strip() for x in rest.rsplit(" | ", 1)]
        aliases = [a.strip() for a in rest.split(",") if a.strip()]
        out.append({"name": name, "zone": zone, "aliases": aliases, "tier": tier})
    return out


def dd_parse_messages(obs: str) -> Dict[str, Dict[str, Any]]:
    """Every customer message seen so far, keyed by job id.

    Each value is {'jid', 'time', 'minute', 'sender', 'text'}. Messages are printed once,
    on the tick they arrive, so this walks the full history rather than the last board.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for line in obs.splitlines():
        m = _DD_MSG.match(line)
        if m:
            jid, t, sender, text = m.groups()
            out[jid] = {"jid": jid, "time": t, "minute": _dd_minutes(t), "sender": sender, "text": text}
    return out


def dd_parse_jobs(obs: str) -> List[Dict[str, Any]]:
    """The 'Open jobs' rows of the last board.

    Each job is {'jid', 'time', 'minute', 'sender', 'zone', 'skill', 'prio', 'status',
    'tech', 'state'}; 'zone'/'skill'/'prio' are None where the board shows '?' (the game
    has not confirmed them), and 'status' is one of unassigned / queued / travelling /
    rerouting / in_progress.
    """
    jobs = []
    for line in _dd_rows(_dd_last_board(obs), "Open jobs:"):
        m = _DD_JOB.match(line)
        if not m:
            continue
        jid, t, sender, zone, skill, prio, state = m.groups()
        tech = re.search(r"\bT(\d+)\b", state)
        if state.startswith("UNASSIGNED"):
            status = "unassigned"
        elif state.startswith("in progress"):
            status = "in_progress"
        elif "queued for" in state:
            status = "queued"
        elif "wrong site" in state:
            status = "rerouting"
        else:
            status = "travelling"
        jobs.append({
            "jid": jid, "time": t, "minute": _dd_minutes(t), "sender": sender.strip(),
            "zone": None if zone == "?" else zone,
            "skill": None if skill == "?" else skill,
            "prio": int(prio[1:]) if prio.startswith("P") else None,
            "status": status, "tech": f"T{tech.group(1)}" if tech else None, "state": state,
        })
    return jobs


def dd_parse_techs(obs: str) -> List[Dict[str, Any]]:
    """The 'Technicians' rows of the last board.

    Each technician is {'tid', 'name', 'skills': [...], 'zone', 'dest', 'activity', 'job',
    'until', 'queue': [...], 'off_shift', 'shift_end'}. 'activity' is idle / travel / work /
    wasted; 'zone' is where they are and 'dest' where they are heading (None unless
    travelling); 'shift_end' comes from the roster in the episode header, in minutes after
    08:00, and is when their own overtime starts.
    """
    roster = {m.group(1): _dd_minutes(m.group(3))
              for m in (_DD_ROSTER.match(l) for l in obs.splitlines()) if m}
    techs = []
    for line in _dd_rows(_dd_last_board(obs), "Technicians:"):
        m = _DD_TECH.match(line)
        if not m:
            continue
        tid, name, skills, arrow, place, doing, queue = m.groups()
        off = queue.endswith(_DD_OFF_SHIFT)
        if off:
            queue = queue[:-len(_DD_OFF_SHIFT)]
        if doing.startswith("travelling"):
            activity = "travel"
        elif doing.startswith("working on"):
            activity = "work"
        elif doing.startswith("on site"):
            activity = "wasted"
        else:
            activity = "idle"
        job = re.search(r"\b(J\d+)\b", doing)
        until = re.findall(r"\d{2}:\d{2}", doing)
        techs.append({
            "tid": tid, "name": name, "skills": skills.split("/"),
            "zone": None if arrow == "->" else place,
            "dest": place if arrow == "->" else None,
            "activity": activity, "job": job.group(1) if job else None,
            "until": _dd_minutes(until[-1]) if until else None,
            "queue": [] if queue.strip() == "-" else [q.strip() for q in queue.split(",") if q.strip()],
            "off_shift": off, "shift_end": roster.get(tid),
        })
    return techs

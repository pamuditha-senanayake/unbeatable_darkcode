"""The model, as a helper to the rules: read one ticket the rules are unsure of.

Everything here fails closed. No gateway configured, no `openai`/`pydantic`, a timeout, a
refused connection, a reply that is not JSON, a trade, zone or job id that does not exist:
each of these gives `None`, and the agent carries on with what the rules read.

Answers ride along in the reply text, outside the brackets ("llm J5=1,HVAC,-,-,0,90 ;"),
so a restarted agent finds them in the history and never pays for the same ticket twice.
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from typing import Optional

MODEL = "arena-small"
EFFORT = "minimal"
CALL_TIMEOUT = 20.0         # seconds per call
TICK_BUDGET = 25.0          # seconds of model time per reply, all calls together (limit is 45)
MAX_TOKENS = 400            # minimal reasoning plus a ~60-token JSON answer
MAX_FAILURES = 3            # failed calls with not one answer: the gateway is down, stop asking
MAX_CALLS = 12              # per shift, whatever happens

try:
    from openai import OpenAI
    from pydantic import BaseModel, ConfigDict, Field

    class Reading(BaseModel):
        model_config = ConfigDict(extra="ignore")
        priority: Optional[int] = Field(default=None, ge=1, le=3)
        trade: Optional[str] = None
        zone: Optional[str] = None
        repeat_of: Optional[str] = None
        not_a_job: bool = False
        confidence: float = Field(default=0.0, ge=0.0, le=1.0)
except Exception:                                   # rules only
    OpenAI = None
    Reading = None

SYSTEM = (
    "You read one customer message for a field-service dispatcher. Reply with one JSON object "
    "and nothing else: {\"priority\": 1|2|3|null, \"trade\": string|null, \"zone\": string|null, "
    "\"repeat_of\": string|null, \"not_a_job\": true|false, \"confidence\": 0.0-1.0}. "
    "priority: 1 = emergency (people stuck, goods about to be lost, the business stopped), "
    "2 = should be done today, 3 = no rush; negations count (\"not critical\" is 3). "
    "trade: the trade that fixes the cause, not the symptom, from the list given. "
    "zone: the zone of the directory customer who sent it, only if the message names that "
    "customer, a nickname or a clear paraphrase; a place mentioned for another reason (a driver, "
    "another branch, the caller's office) is not the site; otherwise null. "
    "repeat_of: one of the listed open jobs if this message follows up on it, else null. "
    "not_a_job: true only if no service is requested at all. Use null when unsure.")

NOTES = re.compile(r"llm((?: J\d+=[^ ;\[\]]+)+) ;")


def client():
    """A client for the arena gateway, or None when it is not configured (a laptop)."""
    base, key = os.environ.get("ARENA_LLM_BASE_URL"), os.environ.get("ARENA_LLM_API_KEY")
    if OpenAI is None or not base or not key:
        return None
    return OpenAI(base_url=base, api_key=key, timeout=CALL_TIMEOUT, max_retries=0)


def prompt(jid, sender, text, directory, trades, zones, candidates):
    lines = ["Message %s from %s: \"%s\"" % (jid, sender, text), "",
             "Directory (customer | zone | nicknames):"]
    lines += ["  %s | %s | %s" % (c["name"], c["zone"], ", ".join(c["aliases"])) for c in directory]
    lines += ["", "Trades: " + ", ".join(trades), "Zones: " + ", ".join(zones)]
    if candidates:
        lines += ["", "Open jobs it may follow up on:"]
        lines += ["  %s: \"%s\"" % (cid, ctext[:120]) for cid, ctext in candidates]
    return "\n".join(lines)


def read(cl, user_prompt):
    """One call. Returns a Reading, or raises."""
    out = cl.chat.completions.create(
        model=MODEL, messages=[{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": user_prompt}],
        reasoning_effort=EFFORT, max_completion_tokens=MAX_TOKENS,
        response_format={"type": "json_object"}, timeout=CALL_TIMEOUT)
    content = (out.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", content, re.S)          # tolerate a stray code fence
    return Reading.model_validate(json.loads(m.group(0) if m else content))


def checked(r, trades, zones, job_ids):
    """Drop whatever the game would reject: a trade nobody has, a zone off the travel table,
    a job id that is not one of the open candidates."""
    by_trade = {t.lower(): t for t in trades}
    by_zone = {z.lower(): z for z in zones}
    return {"prio": r.priority,
            "trade": by_trade.get((r.trade or "").strip().lower()),
            "zone": by_zone.get((r.zone or "").strip().lower()),
            "repeat_of": (r.repeat_of or "").strip().upper() if (r.repeat_of or "").strip().upper() in job_ids else None,
            "not_a_job": bool(r.not_a_job), "confidence": float(r.confidence)}


def encode(jid, v, seconds):
    if v is None:
        return "%s=x" % jid                           # asked, nothing usable: never ask again
    return "%s=%s,%s,%s,%s,%d,%d,%.1fs" % (jid, v["prio"] or "-", v["trade"] or "-", v["zone"] or "-",
                                           v["repeat_of"] or "-", int(v["not_a_job"]),
                                           round(v["confidence"] * 100), seconds)


def decode(obs):
    """Every model answer in our earlier replies: {jid: reading or None}."""
    cache = {}
    for line in obs.splitlines():
        if not line.startswith("[Player]"):
            continue
        for m in NOTES.finditer(line):
            for item in m.group(1).split():
                jid, _, v = item.partition("=")
                if v == "x":
                    cache[jid] = None
                    continue
                f = v.split(",")
                if len(f) < 6:
                    continue
                dash = lambda x: None if x == "-" else x
                try:
                    cache[jid] = {"prio": int(f[0]) if f[0] != "-" else None, "trade": dash(f[1]),
                                  "zone": dash(f[2]), "repeat_of": dash(f[3]),
                                  "not_a_job": f[4] == "1", "confidence": int(f[5]) / 100.0}
                except ValueError:
                    continue
    return cache


_POOL = ThreadPoolExecutor(max_workers=4)


def consult(cl, todo, trades, zones, started):
    """Ask about each (jid, prompt, candidate ids) in parallel within the tick budget.
    Returns ({jid: reading or None}, notes). A call still running when the budget is up is
    abandoned and counts as a failure: a hanging gateway costs one slow tick, never a fault,
    and three failures switch the model off for the shift."""
    remaining = TICK_BUDGET - (time.monotonic() - started)
    if not todo or remaining < 1.0:
        return {}, []
    t0 = time.monotonic()
    futures = {_POOL.submit(read, cl, p): (jid, ids) for jid, p, ids in todo}
    done, _ = wait_futures(futures, timeout=remaining)
    took = time.monotonic() - t0
    got, notes = {}, []
    for f, (jid, ids) in futures.items():
        if f not in done:
            f.cancel()
            got[jid] = None
            notes.append(encode(jid, None, took))
            continue
        try:
            v = checked(f.result(), trades, zones, ids)
        except Exception:
            v = None
        got[jid] = v
        notes.append(encode(jid, v, took))
    return got, notes


def healthy(cache):
    """False once the gateway has failed MAX_FAILURES times without a single answer, or the
    shift's call allowance is spent."""
    failed = sum(1 for v in cache.values() if v is None)
    if failed >= MAX_FAILURES and failed == len(cache):
        return False
    return len(cache) < MAX_CALLS

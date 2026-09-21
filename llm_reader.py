"""General LLM reader: one call per new ticket, cached in the reply notes.

Everything here fails closed: no gateway configured, a missing package, a refused
connection, an auth error, a timeout, a reply that is not the JSON asked for, a trade,
zone or job id that does not exist. Each gives no reading (the rules decide), and the
error class is written into the reply notes ("J5=x:APITimeoutError") and to stderr, so a
practice replay shows why a call failed instead of a silent 0 requests.

Answers ride in the reply text, outside the brackets ("llm J5=1,HVAC,-,-,0,90,why ;"), so
a restarted agent finds them in the history and never pays for the same ticket twice.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from typing import Optional

MODEL = "arena-small"
EFFORT = "minimal"
CALL_TIMEOUT = 20.0         # seconds per call
TICK_BUDGET = 30.0          # seconds of model time per reply, counted from the reply's start
MAX_TOKENS = 300            # minimal reasoning plus a ~70-token JSON answer
MAX_FAILURES = 3            # failed calls with not one answer: the gateway is down, stop asking
MAX_CALLS = 40              # per shift, whatever happens (level 2 has ~28 tickets)

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    class Reading(BaseModel):
        """The strict answer. Unknown keys are rejected; the ranges are checked here, the
        names (trade, zone, job id) against the board in `checked`."""
        model_config = ConfigDict(extra="forbid")
        prio: Optional[int] = Field(default=None, ge=1, le=3)
        trade: Optional[str] = None
        zone: Optional[str] = None
        repeat_of: Optional[str] = None
        not_a_job: bool = False
        confidence: float = Field(ge=0.0, le=1.0)
        reason: str = Field(default="", max_length=120)

        @field_validator("reason")
        @classmethod
        def ten_words(cls, v):
            return " ".join(v.split()[:10])
except Exception:                                   # no pydantic: rules only
    Reading = None

SYSTEM = (
    "You read one customer message for a field-service dispatcher. Reply with ONE JSON object and "
    "nothing else, with exactly these keys: {\"prio\": 1|2|3|null, \"trade\": string|null, "
    "\"zone\": string|null, \"repeat_of\": string|null, \"not_a_job\": true|false, "
    "\"confidence\": 0.0-1.0, \"reason\": \"at most 10 words\"}. "
    "prio: 1 = emergency (people stuck, goods or patients at risk, business stopped), 2 = should "
    "be done today, 3 = no rush; a negation counts (\"not critical\" is 3). trade: the trade that "
    "fixes the cause, not the symptom, from the list; a trade the customer says was ruled out "
    "(\"power is fine\") is not it; several systems failing at once is electrical. zone: the zone "
    "of the directory customer who sent it, only if the message names that customer or a nickname; "
    "a place mentioned for another reason (a driver, another branch, the caller's office) is not "
    "the site; otherwise null. repeat_of: one of the listed open jobs if this message follows up on "
    "it, else null. not_a_job: true only if no service is requested (invoice, thanks, wrong "
    "number). Use null when unsure.")

NOTES = re.compile(r"llm((?: J\d+=[^ ;\[\]]+)+) ;")

_state = {"client": None, "key": None, "suffix": "", "plain": False}


def configured():
    return bool(os.environ.get("ARENA_LLM_BASE_URL") and os.environ.get("ARENA_LLM_API_KEY"))


def _client():
    """The client for the gateway named in the environment right now (read at call time)."""
    base = (os.environ.get("ARENA_LLM_BASE_URL") or "").rstrip("/")
    key = os.environ.get("ARENA_LLM_API_KEY") or ""
    if not base or not key:
        return None
    from openai import OpenAI                       # ImportError is reported, not swallowed
    ident = (base + _state["suffix"], key)
    if _state["key"] != ident:
        _state["client"] = OpenAI(base_url=ident[0], api_key=key, timeout=CALL_TIMEOUT, max_retries=0)
        _state["key"] = ident
    return _state["client"]


def prompt(jid, sender, text, directory, trades, zones, same_customer):
    lines = ["Message %s from %s: \"%s\"" % (jid, sender, text), "",
             "Directory (customer | zone | nicknames):"]
    lines += ["  %s | %s | %s" % (c["name"], c["zone"], ", ".join(c["aliases"])) for c in directory]
    lines += ["", "Trades: " + ", ".join(trades), "Zones: " + ", ".join(zones)]
    if same_customer:
        lines += ["", "Open jobs from the same customer:"]
        lines += ["  %s: \"%s\"" % (cid, ctext[:120]) for cid, ctext in same_customer]
    return "\n".join(lines)


def _create(cl, user_prompt):
    kwargs = dict(model=MODEL, messages=[{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": user_prompt}],
                  reasoning_effort=EFFORT, max_completion_tokens=MAX_TOKENS, timeout=CALL_TIMEOUT)
    if not _state["plain"]:
        kwargs["response_format"] = {"type": "json_object"}
    return cl.chat.completions.create(**kwargs)


def read(user_prompt):
    """One call. Returns (Reading, usage) or raises. A 404 retries once with /v1 appended to
    the base URL; a 400 (a parameter the gateway refuses) retries once without response_format."""
    import openai
    cl = _client()
    if cl is None:
        raise RuntimeError("gateway not configured")
    suffix, plain = _state["suffix"], _state["plain"]
    try:
        out = _create(cl, user_prompt)
    except openai.NotFoundError:
        if suffix or (os.environ.get("ARENA_LLM_BASE_URL") or "").rstrip("/").endswith("/v1"):
            raise
        _state["suffix"] = "/v1"                    # this call used the bare URL: retry with /v1
        out = _create(_client(), user_prompt)
    except openai.BadRequestError:
        if plain:
            raise
        _state["plain"] = True                      # this call sent response_format: retry without
        out = _create(cl, user_prompt)
    content = (out.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", content, re.S)          # tolerate a stray code fence
    reading = Reading.model_validate(json.loads(m.group(0) if m else content))
    return reading, getattr(out, "usage", None)


def checked(r, trades, zones, job_ids):
    """Drop whatever does not exist: a trade nobody has, a zone off the map, a job id that is
    not one of the same customer's open jobs."""
    by_trade = {t.lower(): t for t in trades}
    by_zone = {z.lower(): z for z in zones}
    rep = (r.repeat_of or "").strip().upper()
    return {"prio": r.prio,
            "trade": by_trade.get((r.trade or "").strip().lower()),
            "zone": by_zone.get((r.zone or "").strip().lower()),
            "repeat_of": rep if rep in job_ids else None,
            "not_a_job": bool(r.not_a_job), "confidence": float(r.confidence),
            "reason": r.reason}


def _safe(text, limit=60):
    return re.sub(r"[^A-Za-z0-9-]+", "_", text or "").strip("_")[:limit] or "-"


def encode(jid, v, error=None):
    if v is None:
        return "%s=x:%s" % (jid, _safe(error or "none", 30))   # asked, nothing usable: never again
    return "%s=%s,%s,%s,%s,%d,%d,%s" % (jid, v["prio"] or "-", v["trade"] or "-", v["zone"] or "-",
                                        v["repeat_of"] or "-", int(v["not_a_job"]),
                                        round(v["confidence"] * 100), _safe(v["reason"]))


def decode(obs):
    """Every model answer in our earlier replies: {jid: reading or None}."""
    cache = {}
    for line in obs.splitlines():
        if not line.startswith("[Player]"):
            continue
        for m in NOTES.finditer(line):
            for item in m.group(1).split():
                jid, _, v = item.partition("=")
                if v.startswith("x"):
                    cache[jid] = None
                    continue
                f = v.split(",")
                if len(f) < 6:
                    continue
                dash = lambda x: None if x == "-" else x
                try:
                    cache[jid] = {"prio": int(f[0]) if f[0] != "-" else None, "trade": dash(f[1]),
                                  "zone": dash(f[2]), "repeat_of": dash(f[3]),
                                  "not_a_job": f[4] == "1", "confidence": int(f[5]) / 100.0,
                                  "reason": (f[6] if len(f) > 6 else "").replace("_", " ")}
                except ValueError:
                    continue
    return cache


def healthy(cache):
    """False once the gateway has failed MAX_FAILURES times without a single answer, or the
    shift's call allowance is spent."""
    failed = sum(1 for v in cache.values() if v is None)
    if failed >= MAX_FAILURES and failed == len(cache):
        return False
    return len(cache) < MAX_CALLS


_POOL = ThreadPoolExecutor(max_workers=4)
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def consult(todo, trades, zones, started):
    """Ask about each (jid, prompt, candidate ids) in parallel, within what is left of the
    tick budget. Returns ({jid: reading or None}, notes). A call still running when the
    budget is up is abandoned and counts as a failure (one slow tick, never a fault)."""
    remaining = TICK_BUDGET - (time.monotonic() - started)
    if not todo or remaining < 1.0:
        return {}, []
    futures = {_POOL.submit(read, p): (jid, ids) for jid, p, ids in todo}
    done, _ = wait_futures(futures, timeout=remaining)
    got, notes = {}, []
    for f, (jid, ids) in futures.items():
        v, err = None, None
        if f not in done:
            f.cancel()
            err = "budget"
        else:
            try:
                reading, usage = f.result()
                v = checked(reading, trades, zones, ids)
                USAGE["calls"] += 1
                if usage is not None:
                    USAGE["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                    USAGE["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
            except Exception as e:                  # reported, then the rules decide
                err = type(e).__name__
                print("llm_reader: %s on %s: %s" % (err, jid, str(e)[:200]), file=sys.stderr)
        got[jid] = v
        notes.append(encode(jid, v, err))
    return got, notes

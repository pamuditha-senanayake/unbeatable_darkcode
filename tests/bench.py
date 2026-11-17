"""Paired-seed benchmark and acceptance test (local only, never imported by agent.py).

The acceptance test in plain words: given the practice seeds of every level, when the agent
plays each shift, then no reply faults or is invalid, and the mean score is at least the
frozen golden master's.

    python tests/bench.py AGENT LEVEL SEEDS [--oracle prio,trade,zone] [--save NAME] [--vs NAME]

LEVEL is L1..L4 (L3 and L4 are the local shadows). --oracle feeds the agent the hidden truth
for the named fields: a diagnostic, never a legal agent. --save freezes per-seed scores to
tests/golden/NAME.json; --vs compares with a frozen file seed by seed.
"""
import json
import os
import sys
import time
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
GOLDEN = os.path.join(ROOT, "tests", "golden")


def _env(level):
    import l3_shadow
    import l4_shadow
    if level == "L3":
        l3_shadow.register()
    elif level == "L4":
        l4_shadow.register()
    import arena.games  # noqa: F401
    import arena.envs
    gid = "DispatchDesk-" + level
    arena.envs.TEXTARENA_PLAYERS.setdefault(gid, 1)
    return gid


def _inner(env):
    while not hasattr(env, "scn"):
        env = env.env
    return env


def _oracle(Agent, fields, box):
    """A subclass that reads the hidden truth for `fields` (by the message text)."""
    def truth(blob):
        for text, job in box["by_text"]:
            if text in blob:
                return job
        return None

    class Oracle(Agent):
        if "prio" in fields:
            def _priority(self, blob):
                j = truth(blob)
                return j.priority if j is not None and j.kind == "real" else Agent._priority(self, blob)
        if "trade" in fields:
            def _trade(self, blob):
                j = truth(blob)
                return j.skill if j is not None and j.kind == "real" else Agent._trade(self, blob)
        if "zone" in fields:
            def _site(self, blob, directory):
                j = truth(blob)
                return j.zone if j is not None and j.kind == "real" else Agent._site(self, blob, directory)
    return Oracle


def play(args):
    agent_path, level, seed, fields = args
    gid = _env(level)
    from arena.envs import make_env
    import arena.games.dispatchdesk.replay as rp
    Agent = rp._load_agent(__import__("pathlib").Path(agent_path))
    env, _ = make_env(gid)
    env.reset(num_players=1, seed=seed)
    box = {}
    if fields:
        scn = _inner(env).scn
        box["by_text"] = [(j.text.lower(), j) for j in scn.jobs if j.text]
        Agent = _oracle(Agent, fields, box)
    a = Agent()
    if hasattr(a, "reset"):
        a.reset(gid, 0)
    done, slow, faults, invalid, bad_tail, over5 = False, 0.0, 0, 0, 0, 0
    while not done:
        _, obs = env.get_observation()
        t0 = time.monotonic()
        try:
            reply = a(obs)
        except Exception:
            reply, faults = "[wait]", faults + 1
        slow = max(slow, time.monotonic() - t0)
        # contract: every reply ends with [wait] and carries at most five commands
        cmds = [c for c in __import__("re").findall(r"\[[^\]]*\]", reply)]
        bad_tail += int(not reply.rstrip().endswith("[wait]") and len(cmds) < 5)
        over5 += int(len(cmds) > 5)
        last = obs
        done, _ = env.step(reply)
    inner = _inner(env)
    rewards, info = env.close()
    invalid += int(any(v.get("invalid_move") for v in info.values()))
    # the loss breakdown: value lost per true priority, costs, wasted visits
    st = {"lost_p%d" % k: 0.0 for k in (1, 2, 3)}
    st.update({"late_p%d" % k: 0.0 for k in (1, 2, 3)})
    for j in inner.scn.jobs:
        if j.kind != "real":
            continue
        key = ("late_p%d" if j.status == "done" else "lost_p%d") % j.base_priority
        st[key] += j.base_value - j.earned
    st.update(inner.costs)
    st["max"] = inner.scn.max_value
    for tag, pat in (("wrong_site", "wrong site!"), ("wrong_skill", "cannot fix it"),
                     ("dup_visit", "same issue as"), ("noise_visit", "nothing to fix"),
                     ("rejected", "] rejected:"), ("recall", "recalled; continues")):
        st[tag] = last.count(pat)
    return seed, list(rewards.values())[0], slow, faults, invalid, bad_tail, over5, st


def run(agent, level, seeds, fields=(), procs=8):
    with Pool(procs) as pool:
        rows = pool.map(play, [(agent, level, s, tuple(fields)) for s in seeds])
    return sorted(rows)


def seeds_of(spec):
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    agent, level, spec = a[0], a[1], a[2]
    fields = a[a.index("--oracle") + 1].split(",") if "--oracle" in a else []
    rows = run(os.path.abspath(agent), level, seeds_of(spec), fields)
    scores = {r[0]: r[1] for r in rows}
    mean = sum(scores.values()) / len(scores)
    faults = sum(r[3] for r in rows)
    invalid = sum(r[4] for r in rows)
    tails = sum(r[5] for r in rows)
    over = sum(r[6] for r in rows)
    print("%s %s %s%s mean %.4f slowest %.2fs faults %d invalid %d no-wait-tail %d over5 %d" % (
        os.path.basename(agent), level, spec, (" oracle=" + ",".join(fields)) if fields else "",
        mean, max(r[2] for r in rows), faults, invalid, tails, over))
    keys = list(rows[0][7])
    tot = {k: sum(r[7][k] for r in rows) for k in keys}
    mx = tot.pop("max")
    print("  loss share of max value: " + "  ".join("%s %.3f" % (k, tot[k] / mx) for k in keys if k != "max"
                                                     and not k.startswith(("wrong", "dup", "noise", "rej", "recall"))))
    print("  per shift: " + "  ".join("%s %.2f" % (k, tot[k] / len(rows)) for k in
                                      ("wrong_site", "wrong_skill", "dup_visit", "noise_visit", "rejected", "recall")))
    if "--save" in a:
        os.makedirs(GOLDEN, exist_ok=True)
        with open(os.path.join(GOLDEN, a[a.index("--save") + 1] + ".json"), "w") as f:
            json.dump({"level": level, "scores": scores}, f, indent=0)
    if "--vs" in a:
        with open(os.path.join(GOLDEN, a[a.index("--vs") + 1] + ".json")) as f:
            gold = {int(k): v for k, v in json.load(f)["scores"].items()}
        common = [s for s in scores if s in gold]
        diffs = [scores[s] - gold[s] for s in common]
        n = len(diffs)
        md = sum(diffs) / n
        sd = (sum((d - md) ** 2 for d in diffs) / max(1, n - 1)) ** 0.5
        print("  vs %s: diff %+.4f  stderr %.4f  wins %d losses %d  (n=%d)" % (
            a[a.index("--vs") + 1], md, sd / n ** 0.5, sum(d > 1e-9 for d in diffs),
            sum(d < -1e-9 for d in diffs), n))
    ok = faults == 0 and invalid == 0 and tails == 0 and over == 0
    sys.exit(0 if ok else 1)

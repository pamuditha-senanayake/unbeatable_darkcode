"""p1 diagnostics (local only): list every late or missed P1 with its timeline.

    python p1_diag.py AGENT LEVEL SEEDS [--oracle zone,prio,trade] [--show N]
"""
import os
import sys
from collections import Counter
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import bench  # noqa: E402


def play(args):
    agent_path, level, seed, fields = args
    gid = bench._env(level)
    from arena.envs import make_env
    import arena.games.dispatchdesk.replay as rp
    import arena.games.dispatchdesk.env as E
    Agent = rp._load_agent(__import__("pathlib").Path(agent_path))
    env, _ = make_env(gid)
    env.reset(num_players=1, seed=seed)
    inner = bench._inner(env)
    scn = inner.scn
    W = scn.world
    box = {}
    if fields:
        box["by_text"] = [(j.text.lower(), j) for j in scn.jobs if j.text]
        Agent = bench._oracle(Agent, fields, box)
    read = {}                                     # jid -> list of (now, prio read, zone, trade)

    class Spy(Agent):
        def _plan(self, j, zone, trade, prio, *a, **k):
            read.setdefault(j["jid"], []).append((self._now, prio, zone, trade))
            return Agent._plan(self, j, zone, trade, prio, *a, **k)

        def _shift(self, obs):
            self._now = inner.now
            return Agent._shift(self, obs)

    a = Spy()
    if hasattr(a, "reset"):
        a.reset(gid, 0)
    log = {}                                      # jid -> dict of events
    orig_tick = E.DispatchDeskEnv._tick_tech

    def tick_tech(self, t):
        before = (t.current_job, t.activity)
        orig_tick(self, t)
        after = (t.current_job, t.activity)
        if after != before and after[0] is not None and after[0] != before[0]:
            d = log.setdefault(after[0], {})
            d.setdefault("departs", []).append((self.now, t.tid, t.zone, after[1],
                                                W.travel_minutes(t.zone, t.dest) if t.dest else 0))
    E.DispatchDeskEnv._tick_tech = tick_tech
    snaps = {}                                    # now -> techs state and job state
    done = False
    while not done:
        _, obs = env.get_observation()
        now = inner.now
        snaps[now] = {
            "techs": [(t.tid, t.skills, t.zone, t.activity, t.busy_until, t.dest, t.current_job, list(t.queue),
                       t.shift_end) for t in scn.techs],
            "jobs": {j.jid: (j.status, j.assigned_to) for j in scn.jobs if j.jid in inner.delivered}}
        try:
            reply = a(obs)
        except Exception:
            reply = "[wait]"
        snaps[now]["reply"] = reply
        done, _ = env.step(reply)
    E.DispatchDeskEnv._tick_tech = orig_tick
    rewards, _ = env.close()
    jobs = {j.jid: j for j in scn.jobs}
    out = []
    for j in scn.jobs:
        if j.kind != "real" or j.base_priority != 1:
            continue
        deadline = j.sla_deadline(scn.shift_end)
        seen = max(0, -(-j.arrival // 30) * 30)
        rec = {"seed": seed, "jid": j.jid, "arr": j.arrival, "seen": seen, "deadline": deadline,
               "skill": j.skill, "zone": j.zone, "dur": j.duration, "status": j.status,
               "done": j.completed_at, "late": j.status == "done" and j.completed_at > deadline,
               "read": read.get(j.jid, []), "departs": log.get(j.jid, {}).get("departs", [])}
        # first tick it was assigned, and to whom
        rec["assigned"] = next(((n - 30, s["jobs"][j.jid][1]) for n, s in sorted(snaps.items())
                                if j.jid in s["jobs"] and s["jobs"][j.jid][0] != "open"), None)
        # was it in the reply of the tick when seen
        rec["cmd_at_seen"] = snaps.get(seen, {}).get("reply", "")
        # feasibility at `seen` (true durations): each skilled tech, no recall / with recall
        best, best_recall, techs = None, None, []
        st = snaps.get(seen)
        if st:
            for tid, skills, zone, act, busy, dest, cur, queue, send in st["techs"]:
                if j.skill not in skills:
                    continue
                cj = jobs.get(cur)
                if act == "idle":
                    free, pos = seen, zone
                elif act == "work":
                    free, pos = max(seen, busy), zone
                elif act == "travel":
                    free, pos = max(seen, busy) + (cj.duration if cj and cj.status == "assigned" else 0), dest
                    rfree = seen
                    fin_r = rfree + W.travel_minutes(dest, j.zone) + j.duration
                    best_recall = fin_r if best_recall is None else min(best_recall, fin_r)
                else:                                 # wasted
                    free, pos = max(seen, busy), dest or zone
                    if dest and cj is not None and cj.status == "assigned":
                        free += W.travel_minutes(zone, dest) + cj.duration
                fin = free + W.travel_minutes(pos, j.zone) + j.duration
                best = fin if best is None else min(best, fin)
                best_recall = fin if best_recall is None else min(best_recall, fin)
                cprio = cj.base_priority if cj is not None and cj.kind == "real" else None
                ifin = seen + W.travel_minutes(pos if act != "travel" else dest, j.zone) + j.duration
                techs.append((tid, act, cur, cprio, busy, zone, dest, queue, fin, ifin))
        rec["best"], rec["best_recall"], rec["techs"] = best, best_recall, techs
        out.append(rec)
    return seed, list(rewards.values())[0], out


def main():
    a = sys.argv[1:]
    agent, level, spec = a[0], a[1], a[2]
    fields = tuple(a[a.index("--oracle") + 1].split(",")) if "--oracle" in a else ()
    show = int(a[a.index("--show") + 1]) if "--show" in a else 0
    with Pool(8) as pool:
        rows = pool.map(play, [(os.path.abspath(agent), level, s, fields) for s in bench.seeds_of(spec)])
    allp = [r for _, _, rs in rows for r in rs]
    n = len(allp)
    late = [r for r in allp if r["late"]]
    missed = [r for r in allp if r["status"] != "done"]
    print("P1s %d  on time %d  late %d  missed %d  mean score %.4f" % (
        n, n - len(late) - len(missed), len(late), len(missed), sum(r[1] for r in rows) / len(rows)))
    cat = Counter()
    for r in late + missed:
        if r["best"] is None:
            c = "no tech snapshot"
        elif r["best"] > r["deadline"]:
            c = "infeasible (recall ok)" if (r["best_recall"] or 1e9) <= r["deadline"] else "infeasible"
            ok = [t for t in r["techs"] if t[9] <= r["deadline"]]
            if not ok:
                c += ": structural (idle bound late)"
            elif any(t[3] in (2, 3) for t in ok):
                c += ": blocked by P%d work" % max(t[3] for t in ok if t[3] in (2, 3))
            else:
                c += ": blocked by P1 work"
        else:
            reads = [x[1] for x in r["read"]]
            first = r["read"][0] if r["read"] else None
            if not r["read"]:
                c = "feasible: never planned"
            elif first[0] > r["seen"]:
                c = "feasible: first planned later (held/asked)"
            elif first[1] != 1:
                c = "feasible: read as P%s" % first[1]
            elif first[2] is None:
                c = "feasible: read P1, zone unknown"
            elif first[2] != r["zone"]:
                c = "feasible: read P1, wrong zone"
            elif first[3] != r["skill"]:
                c = "feasible: read P1, wrong trade"
            elif r["assigned"] is None or r["assigned"][0] > r["seen"]:
                c = "feasible: read ok, assigned late"
            else:
                c = "feasible: read ok, assigned at seen"
        r["cat"] = c
        cat[(c, "late" if r["late"] else "missed")] += 1
    for (c, k), v in sorted(cat.items(), key=lambda kv: -kv[1]):
        print("  %-45s %-6s %4d" % (c, k, v))
    # arrival slack: how late the seen tick is
    import statistics
    for label, grp in (("on time", [r for r in allp if not r["late"] and r["status"] == "done"]),
                       ("late", late), ("missed", missed)):
        if grp:
            print("  %-8s mean seen-arr %.1f  mean best-slack %.1f  dur %.1f" % (
                label, statistics.mean(r["seen"] - r["arr"] for r in grp),
                statistics.mean((r["deadline"] - r["best"]) if r["best"] is not None else 0 for r in grp),
                statistics.mean(r["dur"] for r in grp)))
    shown = 0
    for r in late + missed:
        if show and shown >= show:
            break
        if not r["cat"].startswith("feasible"):
            continue
        shown += 1
        print("\nseed %d %s arr %d seen %d dl %d %s@%s dur %d status %s done %s cat %s" % (
            r["seed"], r["jid"], r["arr"], r["seen"], r["deadline"], r["skill"], r["zone"], r["dur"],
            r["status"], r["done"], r["cat"]))
        print("   read:", r["read"][:3], " assigned:", r["assigned"], " departs:", r["departs"])
        print("   best fin %s  recall %s" % (r["best"], r["best_recall"]))
        for t in r["techs"]:
            print("   ", t)
    return allp


if __name__ == "__main__":
    main()

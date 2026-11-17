"""Graph scheduler variant (experiment, round 4 target).

Technicians and jobs form a weighted bipartite graph; an edge is the marginal worth of
inserting the job into that technician's route at its best position (value - travel -
overtime - fees - opportunity). Each round solves a max-weight matching (Hungarian), so
every technician takes the one job that is best for the WHOLE fleet, not the job a fixed
priority order hands it. Rounds repeat until no edge is positive; then relocation and
swap passes polish the routes. Everything else (reading, asks, guards) is agent.Agent.
"""
import time
import agent as base

INF = float("inf")


def hungarian_max(W):
    """Max-weight assignment for an n x m matrix, n <= m. Returns row -> col."""
    n, m = len(W), len(W[0])
    big = max((w for row in W for w in row if w > -INF), default=0.0)
    cost = [[0.0] * (m + 1)] + [[0.0] + [(big - w) if w > -INF else 1e9 for w in row] for row in W]
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0][j] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta; v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
            if j0 == 0:
                break
    return {p[j] - 1: j - 1 for j in range(1, m + 1) if p[j]}


def max_matching(W):
    """Rectangular max-weight matching keeping only positive edges: list of (row, col)."""
    if not W or not W[0]:
        return []
    n, m = len(W), len(W[0])
    if n <= m:
        pairs = hungarian_max(W).items()
    else:
        T = [[W[i][j] for i in range(n)] for j in range(m)]
        pairs = [(i, j) for j, i in hungarian_max(T).items()]
    return [(i, j) for i, j in pairs if W[i][j] > 0]


class Agent(base.Agent):
    SWAP_PASSES = 1
    BUDGET = 8.0          # seconds for the graph search per reply; then keep what we have

    def _routes(self, plans, crew, travel, rules, lam):
        t0 = time.monotonic()
        sim = lambda t, seq: self._simulate(t, seq, travel, rules, lam)[0]
        routes = {t["tid"]: [] for t in crew}
        value = {t["tid"]: 0.0 for t in crew}
        left = list(plans)

        def best_slot(t, p):
            if p["trade"] not in t["skills"]:
                return -INF, None, None
            seq, best = routes[t["tid"]], (-INF, None, None)
            for i in range(len(seq) + 1):
                v = sim(t, seq[:i] + [p] + seq[i:])
                if v - value[t["tid"]] > best[0]:
                    best = (v - value[t["tid"]], i, v)
            return best

        # ---- matching rounds
        while left and crew and time.monotonic() - t0 < self.BUDGET:
            slots = [[best_slot(t, p) for p in left] for t in crew]
            W = [[s[0] for s in row] for row in slots]
            pairs = max_matching(W)
            if not pairs:
                break
            for i, j in pairs:
                t, (_, pos, v) = crew[i], slots[i][j]
                routes[t["tid"]].insert(pos, left[j])
                value[t["tid"]] = v
            taken = {j for _, j in pairs}
            left = [p for k, p in enumerate(left) if k not in taken]

        # ---- relocation passes (as in the base planner)
        order = sorted(plans, key=lambda p: (-rules["value"].get(p["prio"], 0), p["deadline"]))
        for _ in range(getattr(self, "passes", base.INS_PASSES)):
            if time.monotonic() - t0 > self.BUDGET:
                break
            moved = False
            for p in order:
                home = next((t for t in crew if p in routes[t["tid"]]), None)
                if home is not None:
                    routes[home["tid"]].remove(p)
                    value[home["tid"]] = sim(home, routes[home["tid"]])
                best = None
                for t in crew:
                    g, pos, v = best_slot(t, p)
                    if pos is not None and (best is None or g > best[0]):
                        best = (g, t, pos, v)
                if best and best[0] > 0:
                    routes[best[1]["tid"]].insert(best[2], p)
                    value[best[1]["tid"]] = best[3]
                    moved = moved or best[1] is not home
                elif home is not None:
                    moved = True
            if not moved:
                break

        return self._polish(routes, crew, travel, rules, lam, t0)

    def _polish(self, routes, crew, travel, rules, lam, t0=None):
        t0 = t0 or time.monotonic()
        sim = lambda t, seq: self._simulate(t, seq, travel, rules, lam)[0]
        value = {t["tid"]: sim(t, routes[t["tid"]]) for t in crew}
        for _ in range(self.SWAP_PASSES):
            improved = False
            for a in range(len(crew)):
                for b in range(a + 1, len(crew)):
                    ta, tb = crew[a], crew[b]
                    ra, rb = routes[ta["tid"]], routes[tb["tid"]]
                    for x in range(len(ra)):
                        for y in range(len(rb)):
                            if time.monotonic() - t0 > self.BUDGET:
                                break
                            pa, pb = ra[x], rb[y]
                            if pb["trade"] not in ta["skills"] or pa["trade"] not in tb["skills"]:
                                continue
                            na, nb = ra[:x] + [pb] + ra[x + 1:], rb[:y] + [pa] + rb[y + 1:]
                            va, vb = sim(ta, na), sim(tb, nb)
                            if va + vb > value[ta["tid"]] + value[tb["tid"]] + 1e-6:
                                routes[ta["tid"]], routes[tb["tid"]] = na, nb
                                ra, rb = na, nb
                                value[ta["tid"]], value[tb["tid"]] = va, vb
                                improved = True
            if not improved:
                break
        return routes, sum(value.values())

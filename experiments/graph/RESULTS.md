# Graph scheduler experiment

Not wired into `agent.py`. `graph_agent.py` subclasses `agent.Agent` and replaces only `_routes`.

## Idea

Technicians and jobs form a weighted bipartite graph. An edge is the marginal worth of putting
a job into a technician's route at its best position (value minus travel, overtime, fees and
opportunity cost, from the existing `_simulate`). Each round solves a max-weight matching
(Hungarian, pure Python), so each technician takes the job best for the whole fleet, not the
one a fixed priority order hands it. Rounds repeat until no edge is positive. Then the existing
relocation passes run, plus one swap pass that exchanges two jobs between two routes.
An 8 second budget guard stops the search early and keeps what it has.

## Results (baseline = commit 575c01f, paired seeds, mean score)

| Level | Seeds | Baseline | Graph | Diff | Std err | Wins / losses |
|---|---|---|---|---|---|---|
| L4 shadow | 51-200 | 0.6281 | 0.6354 | +0.0073 | 0.0027 | 78 / 65 |
| L4 shadow | 1-50 | 0.6353 | 0.6435 | +0.0082 | 0.0048 | 28 / 19 |
| L3 shadow | 51-200 | 0.6570 | 0.6604 | +0.0034 | 0.0026 | 79 / 61 |
| L2 | 51-200 | 0.6733 | 0.6766 | +0.0033 | 0.0020 | 27 / 20 |
| L1 | 1-50 | 0.7024 | 0.7029 | +0.0006 | 0.0008 | 2 / 1 |

Split on L4 held-out: swap pass alone +0.0022; matching construction supplies the rest.
More swap passes (3) added nothing. Slowest reply 0.08 s, 0 faults.

Caveat: the L4 shadow has no paraphrase bank, so it tests scheduling on the L4 board, not reading.

## Run

    python experiments/graph/eval.py agent.py L4 51-200
    python experiments/graph/eval.py experiments/graph/graph_agent.py L4 51-200

## Integrate (when ready)

Copy `hungarian_max`, `max_matching`, `Agent._routes` and `Agent._polish` from `graph_agent.py`
into `agent.py` (replacing `Agent._routes`), keep `INF` and `import time`, then rerun L1 to L4.

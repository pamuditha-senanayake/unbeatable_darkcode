# harness/

Two things, neither of them submitted.

## `codefest_arena-1.0.0-py3-none-any.whl`

The local harness. Install it once:

```bash
pip install harness/codefest_arena-1.0.0-py3-none-any.whl
```

It gives you `codefest play`, `codefest validate`, `codefest replay` and `codefest compare`.
See the README at the top of this repository.

Do not put this file in `requirements.txt`. That file takes only the approved exact pins printed
in it, and the competition runtime already has the packages your agent may use. A local path
there fails dependency validation and the attempt is rejected.

## `references/`

The agents the rules sheet points at. They are installed with the harness as well, so you can
play against them by name:

```bash
codefest compare --against dd_careful dd_eager
```

They are copied here so you can read them without digging through `site-packages`.

| File | What it is | Scores about |
|---|---|---|
| `dd_careful.py` | Reads tickets properly and refuses work it cannot finish. No model. | 0.35 on level 2 (0.50 on level 1, 0.27 on level 3); means over many seeds, one shift varies a lot |
| `dd_eager.py` | Dispatches on the first word it recognises. | 0.05 |
| `dd_asker.py` | Asks the customer about everything. | 0.02 |
| `common.py` | The board parsers. `dd_parse_jobs`, `dd_parse_techs`, `dd_parse_messages`, `dd_parse_directory`, `dd_travel`, `dd_now`. | not an agent |

You may copy any of this into your own `agent.py`, or import it. `common` is importable
everywhere your agent runs, on your laptop and in the competition:

```python
from common import dd_parse_jobs, dd_parse_techs
```

`dd_careful.py` imports it exactly that way, so a copy of that file runs as it stands.

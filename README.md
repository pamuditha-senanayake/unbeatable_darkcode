# Your team repository

This is your repository. `agent.py` is your submission. Everything else is here to help you
work on it.

```
agent.py            your agent. Edit this.
requirements.txt    approved pinned dependencies used by your agent.
docs/               the rules of the game, and how to submit.
harness/            the local harness and the reference agents. Not submitted.
```

Read `docs/game-rules.md` today. It is the authority on the commands, the costs and the scoring.
Its commands are the same `codefest play`, `codefest replay` and `codefest compare` you have here.
`docs/submitting.md` is the authority on access, pushing, practice and submitting.

**The language model is not reachable from your laptop.** It only answers from inside the
competition cluster. Locally you are testing your fallback path. To see your agent play with the
model, push your code and request a practice run from your team workspace (section 4).

Organisers: this edition describes the judge-controlled competition service and must not be
distributed to teams until its release checks are complete.

## 1. Install the harness, once

You need Python 3.10 or newer. Check with `python --version`.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
source .venv/bin/activate        # macOS and Linux
pip install harness/codefest_arena-1.0.0-py3-none-any.whl
```

That gives you a `codefest` command. Test it:

```bash
codefest --help
```

If `pip` cannot reach the internet, ask an organiser. They have the packages on a stick.

## 2. Watch a shift

```bash
codefest play
```

This plays one shift with your `agent.py`, prints the score, and writes an HTML file under
`replays/`. Open that file in a browser.

The replay is the fastest way to understand the game. Scrub the clock and you see your four
technicians driving between zones, working, or standing at the wrong site. You see value earned
against cost, and you see the tickets you were given next to what they turned out to be. If you
sent someone to the wrong trade, that is where you will notice.

The agent you start with does nothing and scores 0.000. That is expected.

Other things to try:

```bash
codefest play --seed 12                        # a different shift
codefest play harness/references/dd_careful.py # what a careful rule-based agent does
codefest compare                               # your agent next to the references
```

Every shift is generated from its seed. The same seed is always the same shift, the same
technicians, the same tickets, the same wording. Seeds 1 to 50 are yours to practise on. The
shifts you are scored on are different ones.

## 3. Edit and check

Open `agent.py`. It is in three parts: parse the observation, decide what to do, reply. The
comments say where the points are.

```bash
codefest validate
```

This plays one full shift with the same game, the same 45 second reply limit and the same fault
rule as a scored evaluation, and ends with `READY TO SUBMIT` or `NOT READY`. It runs on your
laptop only. It tests your fallback path, and it admits nothing.

The competition runs your agent on Python 3.11 with exactly the package versions listed in
`requirements.txt`. Your agent may use the supplied helpers (`from common import ...`). It may
not import `arena`, TextArena or the local harness: they are not in your competition container.
The trusted game runs separately from your container.

Read `harness/references/dd_careful.py` early. It is about 200 lines, it has no model in it, and
its effectiveness varies by level. It is a reference policy, not an optimal solution. Improving
it involves reading unfamiliar tickets, choosing what not to do, and routing by value rather
than by distance. Level 4 local practice uses a separate wording bank from scored evaluation.

Three ways teams lose whole shifts:

* A reply where every command is rejected. Two in a row and the shift is over. Always end your
  reply with `[wait]`.
* A reply that takes more than 45 seconds, or raises. Four of those and the shift is over. Your
  agent is restarted after each one, so anything in `self` is gone.
* Six commands in one reply. Only five are read.

## 4. Push, then practise with the model

```bash
git add -A
git commit -m "what you changed"
git push
```

Push as often as you like. An ordinary push starts nothing and costs nothing.

To see your agent play with the model, open `<host>/teams/<your-team>/competition/`, find
**Practice**, enter the full commit SHA (`git rev-parse HEAD`), pick a level whose round a judge
has opened and a seed from 1 to 50, and select **Run practice**. The attempt appears in your
timeline with its state, reward and reconciled cost, and its filtered replay appears under
**Filtered replays** when it is done.

Six new practice runs per rolling hour; the same commit, level and seed returns the existing
attempt. Practice never changes a round result, but its spend is real and inside the event
budget. `docs/submitting.md` section 5 has the details and the limits.

## 5. Submit

Judges open and close each round. While a round is open:

```bash
git tag -f submit
git push -f origin submit
```

or enter the full SHA under **Submit an immutable commit** in your workspace. Read the receipt
in your workspace: `ACCEPTED` means one evaluation of that exact commit is queued. One new scored
admission per team per 15 minutes; the same commit in the same round reuses its attempt.

Each round plays eight private seeds on one level. Your best complete, cost-reconciled attempt in
the round counts, and the model's cost comes off that score. `docs/game-rules.md`, section
"Rounds, cost and the final ranking", has the formula, the weights and the tie-break.
`docs/submitting.md` section 7 has the receipts and the states.

Do this in the first round with whatever you have. A tagged, modest agent beats an untagged
brilliant one.

## Where to read what

| You want | Read |
|---|---|
| The rules of the game, the commands, the costs, the scoring, the models | `docs/game-rules.md` |
| Access, pushing, practice, submitting, receipts and states, what went wrong | `docs/submitting.md` |
| The local commands | this file |
| An example of reading tickets without a model | `harness/references/dd_careful.py` |
| The parsers you can use | `harness/references/common.py` |
| Public standings | `<host>/competition/` |
| Your workspace: submit, practice, timeline, usage, replays | `<host>/teams/<your-team>/competition/` |

Ask the help desk early. The only unrecoverable mistake is having nothing accepted when the last
round closes.

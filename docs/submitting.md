# How to submit your agent

Organisers: this edition describes the judge-controlled competition service and must not be
distributed to teams until its release checks are complete.

The other two documents: `game-rules.md` is the game, and the `README.md` at the top of your own
repository is the local loop. This one is about getting access, pushing, practising with the
model, and submitting.

## 1. What you are building

One file: `agent.py`. It holds a class called `Agent`. Once per turn we call your agent with the
whole game so far as a string, and your agent returns its move as a string.

```python
class Agent:
    def __init__(self):
        ...                                   # set-up, once

    def reset(self, game_id, player_id):      # optional, called at the start of every game
        ...

    def __call__(self, observation: str) -> str:
        return "[wait]"                       # your move
```

The game is DispatchDesk: you run a field-service dispatch desk for one shift. There is one game and
nothing else all day, but it comes in **five levels**. A level is a harder shift: more jobs, more
technicians, messier tickets, and in the optional last one a rule that changes. Every level takes
the same commands and prints the same board, so **one agent plays all of them**. You never write a
second agent; yours simply scores less on the harder ones.

You start with levels 1 and 2. Later levels arrive in your repository when the judges announce
them (section 7c). Level 5 is optional, off by default, and withheld until its round if it is
enabled.

Your reply each turn is up to five commands in square brackets, for example:

    [merge J8 J4] [assign T4 J4 Kista] [ask J5 location] [ignore J6] [wait]

**`game-rules.md` is the authority on the commands** - which ones exist, what arguments each one
takes and in what order, and what everything costs. Read it before you write any parsing; the game
prints the same list at the top of every turn.

Two things that cost teams shifts in every rehearsal we ran:

* **Always end your reply with `[wait]`.** If every command in your reply is rejected, that counts as
  an invalid move, and two invalid moves in a row lose the shift. `[wait]` is always accepted.
* **Answer in time.** You have 45 seconds per reply. A reply that is late or raises an exception is
  played as `[wait]` for that tick and counts as a fault; the fourth fault in one shift forfeits it,
  as do two invalid replies in a row. The exact rules are in the rules sheet.

## 2. Getting access

At kickoff you get a card, handed over in person. It carries the exact addresses; the ones below
show the shape.

```
  web address   https://<address>/
  your pages    https://<address>/teams/team-<yours>/competition/      your team workspace
  clone         git clone https://<address>/git/<org>/team-<yours>.git
  username      team-<yours>
  password      on the card
  support       who to ask, and where they sit
```

One credential opens three things: `git push`, your repository page
(`https://<address>/git/<org>/team-<yours>`), and your team workspace. The public standings are at
`https://<address>/competition/` and need no login.

Open the repository address in a browser and sign in with the username and password. You are
looking at your team's own git repository. Nobody else can see it; you cannot see anyone else's.
Then open your pages with the same login. The workspace says no round is open until a judge opens
one.

**If your browser warns about the certificate**, stop and tell an organiser. Do not disable TLS
verification, and do not put your password in a clone URL, a command history or a source file.

## 3. Getting the code

```bash
git clone https://<address>/git/<org>/team-<yours>.git      # the exact command is on your card
cd team-<yours>
```

Git will ask for your username and password the first time. Your repository starts with:

* `agent.py` - a skeleton in three parts: parse the observation, decide, reply. It replies `[wait]`
  every turn, which is legal, passes validation and scores zero. That is your starting point.
* `requirements.txt` - the approved Python packages your agent may use (section 6).
* `README.md` - the local loop: install, play, check.
* `harness/` - the local harness as a `.whl`, and the reference agents to read and play against.
* `docs/` - this file and the rules sheet.

Everything you need is in that clone. There is nothing else to download.

## 4. Checking your agent yourself

Install the harness once (the README in your repository has the three lines), then:

```bash
codefest validate
```

This plays one full shift with your agent and prints `READY TO SUBMIT` or `NOT READY`. It uses the
same game, the same 45-second reply limit and the same fault rule as a scored evaluation. It runs
on your laptop only and admits nothing.

To play a particular level, name it:

```bash
codefest play --game DispatchDesk-L2 --seed 7
codefest validate --game DispatchDesk-L1 --seed 7 --render    # prints every board and reply
```

`codefest play --help` shows the default level, the newest one in your copy of the harness. The
env ids are `DispatchDesk-L1` and `DispatchDesk-L2`; a reveal (section 7c) adds the next one.

Two differences on your laptop:

* **The language model only answers inside the competition cluster.** On your laptop a model call
  fails, so make sure your agent still returns a legal move when one raises. What you are testing
  locally is your fallback path. To see your agent play *with* the model, request a practice run:
  section 5.
* Your laptop is faster or slower than a cluster pod. Do not sail close to the timeout.

Check locally before you spend model budget on a practice run or a scored attempt.

## 5. Pushing and practice

```bash
git add -A
git commit -m "what you changed"
git push
```

Push as often as you like. An ordinary push starts nothing and costs nothing. Nothing appears
after it, and that is expected.

To see your agent play with the model, request a practice run. Open your workspace, find
**Practice**, and enter:

* the full 40-character SHA of a commit you have pushed (`git rev-parse HEAD` prints it),
* a level whose round a judge has opened at least once (the list shows only those),
* a seed from 1 to 50.

Select **Run practice**. The receipt says `ACCEPTED` and the attempt appears in your **Submission
timeline**. Its state moves from `RECEIVED` (waiting for a slot, which can take many minutes while scored
work runs) through `RUNNING` and `ACCOUNTING` to `SCORED`; its reward and its
reconciled cost are shown; and a link appears under **Filtered replays**.

The limits:

* Six new practice runs per rolling hour. The seventh gets `PRACTICE_RATE_LIMITED` until the
  oldest of the six is an hour old.
* The same commit, level and seed returns the existing attempt (`REUSED`), not a new model sample.
* At most two of your attempts, practice or scored, can be unfinished at once. A third gets `BUSY`.
  A `FAILED_PLATFORM` attempt counts as unfinished until a judge retries or excludes it.
* Scored work runs first, and two practice runs execute at a time across all teams, so a practice
  run can wait in the queue while a round is busy.
* A paused round accepts no practice on its level until the judges resume it.

Practice is off-score: it never changes a round result. It uses its own evaluation key, and its
spend is shown separately under **All-attempt evaluation usage**. That spend is real money inside
the event safety budget, so ask for a practice run when it will tell you something.

A practice replay shows each turn's board and the game commands recorded from your reply. Text
outside the brackets is not shown. A turn your agent lost to a fault is marked. If the replay
cannot be reconstructed, no replay is shown and the result is unchanged. Practice replays are
visible to your team and the judges only.

Your laptop and the cluster differ in two ways: the model answers only in the cluster, and a
cluster pod can be slower than your laptop. `READY TO SUBMIT` locally and a forfeit in the cluster
usually means a model call that timed out or raised. A model call that times out inside your
agent is a fault, not a platform failure: the game only sees that your reply was late. Keep your
own client timeout well under 45 seconds and return `[wait]` when it fires.

## 6. Extra Python packages

The competition runs your agent on Python 3.11 with exactly these packages available:

```
openai==3.14.1
httpx==0.28.1
pydantic==2.13.5
numpy==2.1.0
requests==2.34.2
tenacity==9.0.0
python-dotenv==1.2.3
```

List the ones you use in `requirements.txt`, one exact pin per line, copied from that list. No
other package, no version range, no URL, no local path, no pip option. Nothing is installed during
evaluation; the file is checked against the prepared runtime, and a line that does not match makes
the attempt `FAILED_AGENT` with no result and no cost.

**Do not list the harness wheel.** It is for your laptop only. Your competition container holds
your repository, the HTTP wrapper and the approved helpers (`from common import ...` works). It
does not hold `arena`, TextArena, the game source or other teams' files; importing the local
harness in your submission fails there.

What validation accepts, in one list:

* `agent.py` at the repository root, holding `class Agent`, at most 512 KiB.
* Any other files you like next to it, up to about a thousand files of at most 4 MiB each, 32 MiB
  unpacked, 16 MiB as an archive.
* `requirements.txt` as above, at most 16 KiB.
* No `server.py` and no `Dockerfile` at the root. No `.env` file anywhere.

## 7. Submitting

Pushing is not submitting. A scored attempt is admitted in one of two ways, and only while a
judge has a round open:

```bash
git tag -f submit
git push -f origin submit
```

or enter the full SHA under **Submit an immutable commit** in your workspace. Both do the same
thing: the controller pins that exact commit and queues one evaluation of it.

Every decision, admitted or not, is written to your workspace under **Admission receipts**. Read
it there. An admitted attempt also gets a status on the commit in your repository, named
`arena/<event>/<round>` (practice runs post theirs under `arena/<event>/<round>/practice`), that
follows its state and links back to your workspace. A tag push that
was not admitted gets no commit status and is not scheduled later; the receipts table is the only
place it shows. A push before the round opens, or aimed at a level not yet revealed, is refused,
not queued: push again (or enter the SHA) once the round is open.

| receipt | meaning |
|---|---|
| `ACCEPTED` | Admitted. The attempt has an id and appears in your timeline. |
| `REUSED` | This commit already has an attempt in this round. You get that one, not a new one. A tag push normally shows `ACCEPTED` and then `REUSED` for the same commit, because the repository sends two events for one tag; that is expected. You also get `REUSED` for an already-attempted commit while intake is paused or closed. |
| `RATE_LIMITED` | Less than 15 minutes since your last accepted scored attempt. The workspace shows the seconds until your next new admission. |
| `BUSY` | You already have two unfinished attempts. Wait for one to finish. |
| `ROUND_CLOSED` | No round is open, or intake for this round has closed. |
| `LEVEL_NOT_REVEALED` | The round you named plays a level that has not been revealed yet. |
| `PAUSED` | Judges paused the round. Nothing is admitted until they resume it. |

One new scored admission per team per 15 minutes. The clock runs from your last accepted
admission, not from your push, and retagging the same commit does not reset it. It also runs
from an admission that later failed on our side (`FAILED_PLATFORM`); a judge's retry keeps that
attempt's id and does not start a new clock. A new round may
evaluate the same commit again: submit it again when that round opens.

If your `submit` tag already points at the commit you want to submit, for example because you
tagged before the round opened and got `ROUND_CLOSED`, pushing the tag again sends nothing (git says
`Everything up-to-date`, and no receipt appears). Enter the SHA in your workspace instead, or move
the tag: `git push origin :refs/tags/submit`, then tag and push again.

Judges close intake; the displayed end time is advisory and can be extended. Closing intake does
not cancel an attempt that was already accepted. It runs to the end and counts.

### 7a. What the states mean

| state | meaning |
|---|---|
| `RECEIVED` | Accepted and stored. Waiting for an execution slot. |
| `VALIDATING` | Your pinned commit is being fetched and checked: `agent.py` at the root, `class Agent`, approved pins (section 6). |
| `PREPARING`, `QUEUED` | An isolated evaluation is being set up, or is waiting for capacity. |
| `RUNNING` | Your shifts are being played. The progress column counts finished shifts. |
| `ACCOUNTING` | The shifts are done; the gateway spend is still being reconciled. No official score yet. This is pending, not zero, and it resolves on its own, typically in three to four minutes, even when your agent made no model calls. |
| `SCORED` | Complete, with reconciled cost. If it is your best attempt in the round, it is your round result. |
| `FAILED_AGENT` | Validation rejected your commit (no result), or at least one shift forfeited. A forfeited shift counts 0; if all eight shifts still ran to a result, the attempt is complete and counts with those zeros. Progress `8/8` with gross 0 means every shift forfeited (four faults, or two invalid replies in a row); `codefest validate` shows the faults locally. Progress `0/8` means the commit was rejected before it ran: check that `agent.py` with `class Agent` is at the repository root and that `requirements.txt` follows section 6. A complete `FAILED_AGENT` attempt is eligible as your round result like any other; it is rarely your best. |
| `FAILED_PLATFORM` | Something on our side failed. A judge reviews it. It is not a zero and not an automatic rerun; tell an organiser. |
| `CANCELLED` | Excluded by a recorded judge ruling. |

### 7b. Rounds

Each core round plays one level, rounds 1 to 4 on levels 1 to 4. Round 5 is optional. Only one
round is open at a time, and the next opens after the previous one is finalized. The scoring
rule, the eight private seeds, the weights and the tie-break are in `game-rules.md`, section
"Rounds, cost and the final ranking". The short version: your best complete, cost-reconciled
attempt in the round counts, net of 0.10 per USD of average model cost per shift.

When a round is finalized, the selected attempt of every team becomes visible to every registered
team under **Filtered replays** in the workspace. Before that, only judges see official replays.

### 7c. New levels arrive in your repository

At the judge-announced reveal, organisers push the next level into **your own repository**, as
an ordinary commit that touches `harness/` (and `docs/` when a document changed) and nothing else. Your `agent.py` and your
`requirements.txt` are never touched by it.

When we tell you it has landed:

```bash
git pull
pip install --force-reinstall --no-deps harness/codefest_arena-1.0.0-py3-none-any.whl
```

(Type the file name in full: on Windows, `pip` does not expand `*.whl`. `--no-deps` reinstalls only
the harness, in seconds; without it pip reinstalls every dependency, which takes minutes.) If you
have local commits, `git pull` first: a push made before you pulled is rejected as non-fast-forward,
not lost.

Your repository is locked for the few seconds the push takes, so if a `git push` or `git fetch` of yours fails
at exactly that moment (git reports it as an authentication failure), wait ten seconds and try
again; your password has not changed.

You do not have to change anything to play the new level. Your agent already runs on it. Whether it
runs *well* on it is the interesting part. Practice on the new level opens in the workspace when
the judge opens its round.

## 8. What you may and may not do

* You may use any approach: rules, search, a language model, or a mix. Hand-written logic is allowed.
* You may call the shared language-model gateway. Its address and key are already in your container's
  environment (`ARENA_LLM_BASE_URL`, `ARENA_LLM_API_KEY`, `ARENA_LLM_MODEL`) - read them from the
  environment, never hard-code them. They do not work from your laptop. You may ask for any of the
  three models in `game-rules.md`, section "Models"; your key can call all of them, and
  `ARENA_LLM_MODEL` is the default if you do not choose.
* Your agent has **no other network access**. Inside the arena it can reach the gateway and nothing
  else. Any other call fails, including during practice.
* **Do not bring your own model keys.** They will not work, and using a personal frontier-model key
  is against the rules.
* Extra files next to `agent.py` are fine; import them normally (`from helpers import thing`). You
  may copy anything out of `harness/references/` into your own code.
* Do not add `server.py` or `Dockerfile` at the repository root; those names are reserved and the
  attempt is rejected.
* **The model costs you score.** Net is gross minus 0.10 times the average model cost per shift in
  USD (`game-rules.md`, "Rounds, cost and the final ranking"). A cheap agent that scores well is not
  a side prize, it is how you win. Sending the whole observation to the flagship model every turn is
  the expensive way.

## 9. When something goes wrong

| what you see | what it means |
|---|---|
| `codefest: command not found` | the harness went into a different Python; activate the virtual environment from the README |
| `pip install` cannot reach the internet | ask an organiser, they have the packages on a stick |
| git asks for a password again and again | check the username is your team name exactly; retype the password from the card; then ask an organiser |
| `repository not found` or no password prompt | ask an organiser to check the address; never put credentials in a URL or a plaintext helper |
| nothing appears after an ordinary push | expected; request a practice run, or submit while a round is open |
| `LEVEL_NOT_REVEALED` on a practice request | that level's round has not been opened yet; play it locally meanwhile |
| `PRACTICE_RATE_LIMITED` | six new practice runs per rolling hour; wait until the oldest of the six is an hour old |
| `RATE_LIMITED`, `BUSY`, `ROUND_CLOSED`, `PAUSED` | not admitted; section 7 says what each one means and when to retry |
| `FAILED_AGENT` right after `VALIDATING` | validation rejected the commit: `agent.py` not at the root, no `class Agent`, a pin that is not approved, a reserved file; section 6 |
| `FAILED_AGENT` after `RUNNING` | at least one shift forfeited: four faults, or two invalid replies in a row; reproduce with `codefest validate` and a practice run |
| `ACCOUNTING` for a while | spend reconciliation is waiting for the gateway log to settle; pending, not zero; tell an organiser if it does not resolve |
| `FAILED_PLATFORM` | our side; tell a judge; it is not a zero and not an automatic rerun |
| a push refused briefly during a reveal | wait ten seconds, pull the organiser's update, then push again |
| `unknown env id DispatchDesk-L3` on your laptop | that level has not been revealed yet, or you have not run `git pull` and reinstalled the wheel |
| your net is below your gross | that is the model cost coming off it; the workspace shows the average cost next to the net |

Errors that are not receipts (the request was malformed, so nothing was recorded): `FULL_SHA_REQUIRED`
(give all 40 characters), `EXPLICIT_ROUND_REQUIRED` (name the round), `INVALID_PRACTICE_SEED` (1 to
50), `COMMIT_NOT_IN_REGISTERED_REPOSITORY` (push the commit first), `ORIGIN_OR_CSRF_REJECTED` (use
the workspace page, or send the page's headers).

## 10. Who to ask

* The help desk in the room, for your laptop, git, the harness and the rules.
* The FAQ the organisers keep; ask them to check it.
* A judge, for a `FAILED_PLATFORM` attempt, a receipt you think is wrong, or a rule question.

A platform incident during a round is a judge matter. Judges can extend or pause a round while
it is handled. They do not invent scores, and they do not rerun a finished shift. Confirm an
`ACCEPTED` scored receipt before intake closes, and ask early.

# DispatchDesk: game rules for teams

One game, five levels. Same commands and same board at every level. Levels 1 and 2 are in your
kit from the start. Later levels arrive in your repository when the judges announce them. Judges
open and close each round; the displayed times are tentative. Level 5 is optional, disabled by
default, and withheld until its round if it is enabled.

Organisers: this edition describes the judge-controlled competition service and must not be
distributed to teams until its release checks are complete.

Kickoff env ids: `DispatchDesk-L1` and `DispatchDesk-L2`. A reveal adds the next one.

Try it from your team repository: `codefest play --game DispatchDesk-L2` (the README there
explains the one-time install; `codefest validate --render` prints every board and every reply).

## The story

You are the dispatcher of a field-service company for one shift, 08:00 to 17:00. Customers send
messages (tickets). You have technicians with different skills, spread over zones. Every 30
minutes you decide who goes where; then the clock advances. Finished jobs earn value. Travel,
overtime, questions and mistakes cost. At 17:00 the technicians finish what you queued for them
(overtime, until 19:00 at the latest) and the shift is scored.

The tickets are written by customers, not by a database.

* **Urgency is implied**, never stated as a priority. "200 vaccine doses in there" is a P1.
  "No need to hurry, it is not critical" is a P3 even though it contains the word critical. A P1
  is worth ten P3s, so this is the single most valuable thing to read correctly.
* **The site may be missing.** Some messages name the customer. Some only use a nickname from
  the customer directory ("the brewhouse"). Some give nothing at all. Some mention a place that
  is *not* the site ("our driver is coming from Kista").
* **The fault may be ambiguous.** "Wifi, tills and fridges on the east side all dropped at
  once" reads like IT or refrigeration but is an electrical fault. Send the wrong skill and the
  technician wastes the trip.
* **Some messages repeat a job** already reported ("hi again, still nobody here"). Merge them
  or you will send two technicians.
* **Some messages are not service requests** at all. Ignore them.

You can pay a small fee to ask the customer a question. The answer arrives next tick.

## The levels

Every level is one shift of 18 ticks. What changes is the size of the board, the text, and in
the final round the rules.

| Level | Shift | What it tests | When you get it |
|---|---|---|---|
| 1 | 4 technicians, 6 zones, about 11 real jobs, 2 repeats, 1 non-request | reading tickets | kickoff |
| 2 | the same fleet, about 22 real jobs, 4 repeats, 2 non-requests | saying no: there is twice as much work as the fleet can do | kickoff |
| 3 | a bigger city: 10 zones, 23 customers, 8 technicians, about 45 real jobs | scheduling on a board twice the size; tokens and time start to bite | judge-announced reveal |
| 4 | the level 3 shape with harder text | reading tickets a word list cannot read | judge-announced reveal |
| 5 | the level 4 shape plus a rule change | adapting once, unrehearsed | optional final round only |

**The final round changes a rule.** The change is written out in plain words in the rules text
at the top of that episode, in the same place the rules you are reading now appear. An agent
that reads the rules text it is given will cope. An agent that hard-codes level 4 will not.

An agent written for level 1 runs unchanged on level 5 and simply scores less. The board
layout, the header line, the commands, the costs and the value table are the same on every
level. What grows is the intro text (a bigger travel table, a longer directory, a longer
roster) and the number of rows on the board.

## What you see

The observation is the full text history of the episode (TextArena style). It starts with the
rules, the travel-time table, the customer directory and the technician roster, then one board
per tick. The **last board** is the current state. Messages are printed in full once, on the
tick they arrive; afterwards only the job row remains, so keep your own notes (or re-read the
history, it is all in the observation).

```
[GAME] === 09:30 | tick 4/18 | earned 50 | costs 32 (travel 32, overtime 0, fees 0, penalties 0) ===
Events:
  09:21 Elin (T3) finished J8 (P2, SLA 12:24): on time, +40.
New messages:
  J11 09:14 from Jonas, Sundbyberg Municipal Pool: "One phase is dead in the kitchen, half the equipment is off. Whenever convenient, sometime today is fine."
Open jobs:
  J1   07:44  from reception, Nacka Marina Hote  zone ?           skill ?             prio ?   UNASSIGNED
  J3   07:54  from unknown number                zone Kista       skill lift          prio ?   in progress: T1 until 09:56
  J4   08:11  from no caller id                  zone Sundbyberg  skill refrigeration prio ?   in progress: T4 until 10:21
  J5   08:17  from front desk, Solna Health Cli  zone ?           skill ?             prio ?   UNASSIGNED
  J7   08:23  from Ola, Soder Brewery            zone ?           skill ?             prio ?   queued for T4 (position 1)
  J9   08:56  from Lena, Kista Logistics AB      zone ?           skill ?             prio ?   queued for T1 (position 1)
  J11  09:14  from Jonas, Sundbyberg Municipal   zone ?           skill ?             prio ?   UNASSIGNED
Technicians:
  T1 Amir   lift/IT                    at Kista        working on J3 until 09:56                queue: J9
  T2 Hugo   refrigeration/lift         at Nacka        idle                                     queue: -
  T3 Elin   electrical/HVAC            at Sodermalm    idle                                     queue: -
  T4 Bea    refrigeration/plumbing     at Sundbyberg   working on J4 until 10:21                queue: J7
Your commands (max 5), e.g. [assign T1 J3 Solna] [ask J4 location] [merge J5 J2] [ignore J6] [wait]:
```

`zone`, `skill` and `prio` show only what the game has **confirmed** (through an answer or a
technician's visit). `?` means you have to infer it from the message.

The zones, the travel table (minutes) and the customer directory (name, zone, nicknames) are in
the first message of every episode. Read them from there, not from a copy: level 3 plays in a
different city.

## What you say

Reply with one or more commands, each in its own square brackets, up to 5 per tick. Anything
outside brackets is ignored.

| Command | Effect |
|---|---|
| `[assign T2 J7 Kista]` | Queue J7 for technician T2, who travels to Kista. The zone is required unless the job row already shows one. Jobs run in queue order. Re-assigning a queued job moves it. |
| `[unassign J7]` | Take J7 out of a queue. A technician already driving to it is recalled and continues from the destination (the drive is a sunk cost); one standing at a wrong site stays there and goes idle when the 15 min are up. Impossible once work has started. |
| `[ask J7 location]` `[ask J7 problem]` `[ask J7 urgency]` | The customer answers next tick and the answer is recorded on the job row. Fee 5 each. |
| `[merge J9 J7]` | Declare J9 a repeat of J7. Right: J9 closes. Wrong: penalty 15, both stay open. |
| `[ignore J9]` | Close J9 for good. For messages that are not service requests. Ignoring a real job forfeits its value. |
| `[wait]` | Do nothing this tick. |

Rejected commands (unknown job, missing zone, a technician who is off shift) are reported back
and cost nothing. A reply with **no valid command at all** is an invalid move. Two invalid moves
in a row forfeit the episode (score 0).

## Time limits and forfeits

| | |
|---|---|
| Time per reply | **45 seconds**, measured by the harness from the moment it sends you the observation to the moment it has your reply. |
| Late reply, or your code raises | That tick is played as `[wait]` for you: nothing is dispatched, the clock moves on, and it counts as one **fault**. Your agent is restarted in the background, so anything you kept in memory is gone. Keep your notes in your reply text (see Hints). |
| Four faults in one episode | The episode is forfeited (score 0). Three is the most you can survive. |
| Two invalid replies in a row | Forfeit (score 0). A reply is invalid only if *every* command in it was rejected, so end every reply with `[wait]`. |
| The whole shift | 18 replies. An episode that runs its course always scores whatever you earned, even if that is 0. |

Why faults do not forfeit at once: the shared model gateway has slow moments that are not your
doing, and a ranking should not be decided by them. Why they still hurt: a lost tick is 30
minutes of nobody being dispatched, and you lose your in-memory state. Fast, compact prompts
pay twice.

**End every reply with `[wait]`.** It is always valid, it costs nothing, and it is the
difference between a bad tick and a lost episode. Commands get rejected for ordinary reasons:
the job finished while you were thinking, the technician went off shift, you referred to a job
id that has just been merged. A synthetic team lost a whole episode in a rehearsal by repeating
`[merge J14 J12]` after J12 had already finished. One trailing `[wait]` makes that impossible.

Example reply: `[merge J8 J4] [assign T4 J4 Kista] [ask J5 location] [ignore J6] [wait]`

## What happens when a technician arrives

* Right zone, right skill: work starts; duration 30 to 90 min depending on the fault.
* Wrong zone: 15 min lost, the true zone is revealed, the technician drives on to it.
* Right zone, wrong skill: 15 min lost, the true skill is revealed, the job returns to
  UNASSIGNED. Dispatch it again, to someone who has the skill. The technician who bounced
  stays on site for those 15 minutes and still cannot fix it, so sending them back burns
  another 15.
* The job was a repeat of another: 15 min lost, the repeat is closed.
* The message was not a service request: 15 min lost, the job is closed.

Technicians work through their queue on their own; you only need to act when something
changes. One technician's shift ends at 15:00, the others at 17:00; work after a technician's
own shift end is overtime. Nobody works past 19:00.

## Scoring an episode

| | |
|---|---|
| Job value | **P1 = 100, P2 = 40, P3 = 10** (true priority, never shown), the same on every level |
| SLA | P1: finished within 2 h of the message time. P2: within 4 h. P3: by 17:00. |
| Late | A job finished after its SLA earns 50 %. Unfinished: 0. |
| Travel | 0.5 per minute on the road |
| Overtime | 1 per minute a technician works or drives after their shift end |
| Question | 5 each |
| Wrong merge | 15 each (a given pair is only charged once) |

**Episode score = (value earned minus all costs) / (total value of all real jobs in the shift),
clipped to [0, 1].** Repeats and non-requests have no value. From level 2 up the shift contains
more work than the fleet can do, so nobody scores anywhere near 1.

Reference scores over 60 to 100 seeds, so you know where you stand:

| Level | perfect-information planner (not a legal agent) | careful rule-based reference | keyword anchor (`heuristic_agent` in `codefest compare`) | random |
|---|---|---|---|---|
| 1 | 0.72 | 0.50 | 0.44 | 0.00 |
| 2 | 0.67 | 0.35 | 0.18 | 0.00 |
| 3 | 0.67 | 0.27 | 0.10 | 0.00 |

The planner reads hidden state and re-plans every technician's route each tick; it marks the
ceiling. The careful reference is `harness/references/dd_careful.py`; it is a legal agent with
no model in the loop.

## Rounds, cost and the final ranking

Each round plays one level: round 1 plays level 1, round 2 plays level 2, and so on to round 4
and level 4. An optional round 5 plays level 5; judges may enable or disable it until it opens.
Judges open a round, may extend it, and close its intake. The displayed end time is advisory. A
scored attempt is admitted when your commit is accepted while the round is open, one new
admission per team per 15 minutes. An attempt accepted before intake closes still runs to the
end and counts. `submitting.md` explains how to submit and what every receipt and state means.

Each round evaluates eight fixed private seeds, the same eight for every team and every attempt
in that round. The same commit in the same round reuses its existing attempt. Your best complete,
cost-reconciled whole attempt counts; shifts from different attempts are never combined.

    gross                   = mean episode reward over the eight seeds
    average evaluation cost = that attempt's reconciled gateway USD / 8
    net                     = gross - 0.10 x average evaluation cost

Net has no floor. Practice runs and attempts that were not selected do not accumulate as a
penalty. Unknown usage is pending, not zero. A platform failure needs a judge's review; it is
neither an automatic zero nor an automatic new model sample.

Round weights are 1:2:3:4, plus 5 if round 5 is enabled, normalized across the enabled rounds.
A round in which you have no result contributes zero and is labelled "no result". Ties at six
decimal places go to the lower average cost of the selected attempts, weighted the same way
across rounds; still tied, the rank is shared. Total development spend is not a tie-break. Human
judges make the final award decision; there is no automated presentation weighting.

Practice uses seeds 1 to 50 and, on level 4, a separate wording bank. Practice spending is shown
privately to your team and still counts against the event safety budget. Budget or capacity
limits can stop model calls or delay a run; do not assume they are unreachable. Filtered practice
replays are private to your team. When judges finalize a round, the selected official attempt of
every team becomes visible to all registered teams. A replay shows the visible boards and the
recorded game commands only; raw logs, seed lists and hidden state stay private. A missing replay
does not change a score.

## Models

Your agent reaches the model gateway only from inside the competition cluster (during
practice and scored matches), never from your laptop. Local development is against the
rule-based game. Three models are offered, by name, at their real price difference:

| Name on the gateway | Model | Input, USD per million tokens | Output, USD per million tokens |
|---|---|---|---|
| `arena-large` | gpt-5.2 | 1.75 | 14 |
| `arena-medium` | gpt-5-mini | 0.25 | 2 |
| `arena-small` | gpt-5-nano | 0.05 | 0.40 |

Two things to know before you pick:

* **These models reason before they answer, and reasoning tokens are output tokens** at eight
  times the input price. Set `reasoning_effort` deliberately (`low` is a good default on the
  large model; `minimal` on the small ones) and cap the output tokens with the 45-second limit
  in mind. Left at the default, the small model spends its whole output budget thinking and
  replies with nothing usable.
* **Time is the flagship's real cost.** The difference between `low` and `medium` effort is
  about ten seconds per reply, which is a third of your limit.

A plain "send the whole observation, ask for commands" prompt on `arena-large` at `low` effort
scores about 0.43 on level 2, well below the careful reference plus a scheduler. The gateway
is the same for everyone; personal model keys are not allowed.

## Determinism and the contract

Everything is generated from the match seed: same seed, same technicians, same tickets, same
wording. The harness plays every team on the same seeds. Your agent is
`class Agent: __call__(observation: str) -> str` with an optional `reset(game_id, player_id)`
called before each episode, a good place to clear per-episode memory. `game_id` tells you which
level you are on.

## Seeing what your agent did

`codefest validate --render` prints every board and reply, which is a lot of text to read. For a
practice seed, this draws the whole shift instead:

```
codefest replay --game DispatchDesk-L2 --seed 7
```

It writes one HTML file and prints its path; open it in a browser. Scrub the shift clock and
you get your technicians moving between zones, their day as a timeline of driving against
working against wasted visits, value earned against cost accrued, and a ticket board showing
what you had been told next to what was actually true. If you sent someone to the wrong trade
or read a P1 as a P3, that is where you will see it.

Practice seeds are 1 to 50. Each scored round uses eight private seeds outside that range,
different for every round, so a replay you studied teaches you about the game and nothing about
the next round's shifts.

To see your agent play *with the model*, request a practice run from your team workspace: a
pushed commit, an opened level and a seed from 1 to 50. `submitting.md` section 5 explains it.
The filtered replay appears in the workspace when the run is done.

To put your agent next to the reference policies over the same shifts:

```
codefest compare --seeds 5
```

`harness/references/dd_careful.py`, `dd_asker.py` and `dd_eager.py` are worth reading before you
start. They are short, they have no model in the loop, and they mark out the ground: careful
reading and a willingness to say no scores about 0.35 on level 2, keyword matching 0.18, asking
the customer everything 0.02, and dispatching on the first word you recognise 0.05.

## Hints

* **Triage before routing.** From level 2 up the question is not "who is nearest" but "which of
  these am I not going to do". A P1 is worth ten P3s and the P3 costs the same drive.
* **Know when to stop.** Overtime costs 1 per minute and a P3 is worth 10, so a job that will
  only finish at 17:30 is usually worth leaving. Queue nothing you cannot pay for.
* **Keep a compact state.** The whole history is in the observation, and on level 3 it reaches
  70 000 characters by the last tick. Build a structured summary of the open jobs (your best
  guess of zone, skill, priority, deadline, and why) and carry it between turns rather than
  sending the raw history each tick. It is faster, it is cheaper, and the cost comes out of your
  score.
* **Read the tickets with a model, schedule in code.** Reading urgency and the site is what a
  model is for; routing eight technicians is what a loop is for. Ask the model once per new
  ticket, not once per tick about everything.
* Asking costs 5 and a tick; a wasted visit costs 15 min of technician time plus the drive.
  Ask when the site is unknown, gamble when the skill is 80 % clear.
* Priority drives the SLA clock, and the SLA clock started when the message arrived, not when
  you read it. Order P1 work first even if a P3 is closer.
* Every command you send is echoed in the history, so a cheap model can keep its own notes in
  the reply text outside the brackets. The game ignores it; the next observation contains it.
* Read the rules text at the top of every episode, every round. The final round changes a
  rule, and it is described there.

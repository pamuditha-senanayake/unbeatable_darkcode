"""Shadow DispatchDesk-L3 until the organisers ship it (local only, never imported by agent.py).

Registers `DispatchDesk-L3` with the configuration the installed wheel documents for level 3
(metro world, 8 technicians, 45 real jobs, 8 repeats, 4 non-requests, v1 values) if the harness
does not already know the id, so the real reveal supersedes it. Run:

    python tests/l3_shadow.py [agent.py] [--seeds 1-50]

and it prints the mean score, the slowest reply and the faults (replies that raised).
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def register():
    import textarena as ta
    from textarena.wrappers import ActionFormattingWrapper, LLMObservationWrapper
    import arena.games  # noqa: F401  - registers L1/L2 (and L3 once it ships)
    from arena.games.dispatchdesk.scenario import PRIORITY_VALUE_V1
    if "DispatchDesk-L3" not in ta.envs.registration.ENV_REGISTRY:
        ta.register(id="DispatchDesk-L3", entry_point="arena.games.dispatchdesk.env:DispatchDeskEnv",
                    default_wrappers=[LLMObservationWrapper, ActionFormattingWrapper],
                    num_techs=8, num_jobs=45, num_dups=8, num_noise=4, values=PRIORITY_VALUE_V1,
                    world="metro")
    import arena.envs
    arena.envs.TEXTARENA_PLAYERS.setdefault("DispatchDesk-L3", 1)


def play(agent_path, seed):
    register()
    from arena.envs import make_env
    import arena.games.dispatchdesk.replay as rp
    Agent = rp._load_agent(__import__("pathlib").Path(agent_path))
    env, _ = make_env("DispatchDesk-L3")
    env.reset(num_players=1, seed=seed)
    a = Agent()
    if hasattr(a, "reset"):
        a.reset("DispatchDesk-L3", 0)
    slow, faults, done = 0.0, 0, False
    while not done:
        _, obs = env.get_observation()
        t0 = time.monotonic()
        try:
            reply = a(obs)
        except Exception:
            reply, faults = "[wait]", faults + 1
        slow = max(slow, time.monotonic() - t0)
        done, _ = env.step(reply)
    rewards, _ = env.close()
    return list(rewards.values())[0], slow, faults


if __name__ == "__main__":
    args = sys.argv[1:]
    agent = next((a for a in args if a.endswith(".py")), os.path.join(ROOT, "agent.py"))
    lo, hi = 1, 50
    if "--seeds" in args:
        lo, hi = map(int, args[args.index("--seeds") + 1].split("-"))
    res = [play(agent, s) for s in range(lo, hi + 1)]
    print("L3 shadow seeds %d-%d: mean %.4f  slowest reply %.2fs  faults %d" % (
        lo, hi, sum(r[0] for r in res) / len(res), max(r[1] for r in res), sum(r[2] for r in res)))

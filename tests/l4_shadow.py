"""Shadow DispatchDesk-L4 (local only, never imported by agent.py).

The real level 4 draws its wording from banks/paraphrases.json, which is not in the kit, so
this registers `DispatchDesk-L4` with every level-4 knob except the paraphrase bank: the L3
board, 70 % vague faults, 45 % decoy places, sites missing or nicknamed more often. It checks
timing, faults and the scheduler under the level-4 shape, not the reading. Run:

    python tests/l4_shadow.py [agent.py] [--seeds 1-50]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = "DispatchDesk-L4"


def register():
    import textarena as ta
    from textarena.wrappers import ActionFormattingWrapper, LLMObservationWrapper
    import arena.games  # noqa: F401
    from arena.games.dispatchdesk.scenario import PRIORITY_VALUE_V1
    if GAME not in ta.envs.registration.ENV_REGISTRY:
        ta.register(id=GAME, entry_point="arena.games.dispatchdesk.env:DispatchDeskEnv",
                    default_wrappers=[LLMObservationWrapper, ActionFormattingWrapper],
                    num_techs=8, num_jobs=45, num_dups=8, num_noise=4, values=PRIORITY_VALUE_V1,
                    world="metro", paraphrase=False, vague_rate=0.7, decoy_rate=0.45,
                    location_styles=["named", "alias", "alias", "alias", "missing", "missing", "missing"])
    import arena.envs
    arena.envs.TEXTARENA_PLAYERS.setdefault(GAME, 1)


def play(agent_path, seed):
    register()
    from arena.envs import make_env
    import arena.games.dispatchdesk.replay as rp
    Agent = rp._load_agent(__import__("pathlib").Path(agent_path))
    env, _ = make_env(GAME)
    env.reset(num_players=1, seed=seed)
    a = Agent()
    if hasattr(a, "reset"):
        a.reset(GAME, 0)
    slow, faults, done, llm_notes, retries = 0.0, 0, False, 0, 0
    while not done:
        _, obs = env.get_observation()
        t0 = time.monotonic()
        try:
            reply = a(obs)
        except Exception:
            reply, faults = "[wait]", faults + 1
        slow = max(slow, time.monotonic() - t0)
        llm_notes += reply.count("=") if reply.startswith("llm ") else 0
        retries += reply.count("=r:")
        done, _ = env.step(reply)
    rewards, _ = env.close()
    return list(rewards.values())[0], slow, faults, llm_notes, retries


if __name__ == "__main__":
    args = sys.argv[1:]
    agent = next((a for a in args if a.endswith(".py")), os.path.join(ROOT, "agent.py"))
    lo, hi = 1, 50
    if "--seeds" in args:
        lo, hi = map(int, args[args.index("--seeds") + 1].split("-"))
    res = []
    for s in range(lo, hi + 1):
        r = play(agent, s)
        res.append(r)
        print("seed %d: score %.4f slowest %.2fs faults %d llm-notes %d retries %d" % ((s,) + r), flush=True)
    print("L4 shadow seeds %d-%d: mean %.4f  slowest reply %.2fs  faults %d" % (
        lo, hi, sum(r[0] for r in res) / len(res), max(r[1] for r in res), sum(r[2] for r in res)))

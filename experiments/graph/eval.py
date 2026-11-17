"""eval.py AGENT GAME SEEDS -> per-seed and mean. GAME in L1 L2 L3 L4 (L3/L4 = shadows)."""
import sys, os, time
ROOT = os.getcwd()  # run from the repo root
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
agent, game, seeds = sys.argv[1], sys.argv[2], sys.argv[3]
lo, hi = map(int, seeds.split("-"))
import l4_shadow, l3_shadow
if game == "L4":
    l4_shadow.register()
elif game == "L3":
    l3_shadow.register()
gid = "DispatchDesk-" + game
import arena.games, arena.envs
from arena.envs import make_env
import arena.games.dispatchdesk.replay as rp
arena.envs.TEXTARENA_PLAYERS.setdefault(gid, 1)
Agent = rp._load_agent(__import__("pathlib").Path(agent))
res, slow, faults = [], 0.0, 0
for s in range(lo, hi + 1):
    env, _ = make_env(gid); env.reset(num_players=1, seed=s)
    a = Agent(); a.reset(gid, 0)
    done = False
    while not done:
        _, obs = env.get_observation(); t0 = time.monotonic()
        try:
            r = a(obs)
        except Exception as e:
            r, faults = "[wait]", faults + 1
        slow = max(slow, time.monotonic() - t0)
        done, _ = env.step(r)
    rw, _ = env.close(); res.append(list(rw.values())[0])
print("%s %s %s mean %.4f slowest %.2fs faults %d" % (os.path.basename(agent), game, seeds, sum(res)/len(res), slow, faults))
print(" ".join("%.3f" % x for x in res))

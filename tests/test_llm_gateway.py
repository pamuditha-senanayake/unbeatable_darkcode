"""The model path against a fake OpenAI-compatible gateway on localhost.

Local only (it needs the harness for real boards): `python -m pytest tests -q` or
`python tests/test_llm_gateway.py`. Each test points ARENA_LLM_* at a tiny HTTP server that
records every request and answers the way that test needs: well, badly, slowly, or not at all.
"""
import http.server
import importlib
import json
import os
import re
import socket
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "harness", "references")]

from arena.envs import make_env        # noqa: E402  (the local harness, never shipped)

UNREADABLE = "Something is not right in the back office, could someone have a look?"


class Gateway:
    """A fake /v1/chat/completions. `answer(body) -> (status, text, delay)`."""

    def __init__(self, answer):
        self.requests, self.answer = [], answer
        gw = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                gw.requests.append(body)
                status, text, delay = gw.answer(body)
                time.sleep(delay)
                reply = {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": text}}],
                         "usage": {"prompt_tokens": 500, "completion_tokens": 60, "total_tokens": 560}}
                data = json.dumps(reply).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass                          # the client gave up (timeout test)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def reading(**kw):
    r = {"priority": None, "trade": None, "zone": None, "repeat_of": None, "not_a_job": False, "confidence": 0.95}
    r.update(kw)
    return json.dumps(r)


def board(seed=4, unreadable="J3"):
    """The first observation of an L2 shift, with one ticket rewritten so the rules can read
    neither its urgency nor its trade (the case the gate is for)."""
    env, _ = make_env("DispatchDesk-L2")
    env.reset(num_players=1, seed=seed)
    _, obs = env.get_observation()
    if unreadable:
        obs = re.sub(r'(  %s \d\d:\d\d from [^:]+: )"[^"]*"' % unreadable, r'\1"%s"' % UNREADABLE, obs)
    return env, obs


def commands(reply):
    return re.findall(r"\[[^\]]*\]", reply)


class ModelPath(unittest.TestCase):
    def agent(self, url="http://127.0.0.1:9/v1", key="test-key"):
        os.environ["ARENA_LLM_BASE_URL"], os.environ["ARENA_LLM_API_KEY"] = url, key
        os.environ["ARENA_LLM_MODEL"] = "arena-large"   # the gateway's default; we pick arena-small
        import llm_reader
        import agent
        importlib.reload(llm_reader)
        importlib.reload(agent)
        self.llm_reader = llm_reader
        return agent.Agent()

    def tearDown(self):
        for k in ("ARENA_LLM_BASE_URL", "ARENA_LLM_API_KEY", "ARENA_LLM_MODEL"):
            os.environ.pop(k, None)
        if getattr(self, "gw", None):
            self.gw.close()

    def valid(self, reply):
        cmds = commands(reply)
        self.assertTrue(cmds and cmds[-1] == "[wait]", reply)
        self.assertLessEqual(len(cmds), 5, reply)

    # ------------------------------------------------------------------ the gate
    def test_call_fires_when_rules_are_unsure_and_fills_the_gap(self):
        self.gw = Gateway(lambda b: (200, reading(priority=1, trade="plumbing"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        asked = [r for r in self.gw.requests if "Message J3 " in r["messages"][1]["content"]]
        self.assertEqual(len(asked), 1, "the unreadable ticket must be sent to the model once")
        body = asked[0]
        self.assertEqual(body["model"], "arena-small")
        self.assertEqual(body["reasoning_effort"], "minimal")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("J3=1,plumbing", reply)
        # the model filled urgency and trade, so there is nothing to ask about them
        self.assertNotIn("[ask J3 problem]", reply)
        self.assertNotIn("[ask J3 urgency]", reply)

    def test_no_call_when_rules_are_sure_after_the_health_check(self):
        self.gw = Gateway(lambda b: (200, reading(), 0))
        a = self.agent(self.gw.url)
        env, obs = board(unreadable=None)
        r1 = a(obs)
        self.valid(r1)
        self.assertEqual(len(self.gw.requests), 1, "exactly one health-check call on the first tick")
        env.step(r1)
        _, obs2 = env.get_observation()
        self.valid(a(obs2))
        # seed 4's second tick brings no ticket the rules cannot read
        self.assertEqual(len(self.gw.requests), 1, "no call for tickets the rules read")

    def test_no_gateway_configured_means_rules_only(self):
        os.environ.pop("ARENA_LLM_BASE_URL", None)
        import llm_reader
        import agent
        importlib.reload(llm_reader)
        importlib.reload(agent)
        a = agent.Agent()
        self.assertIsNone(a.llm)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertNotIn("llm ", reply)
        self.assertIn("[ask J3", reply)           # the rules ask instead

    # ------------------------------------------------------------------ failures fall back
    def test_invalid_json_falls_back_to_rules(self):
        self.gw = Gateway(lambda b: (200, "sure! it is probably the boiler", 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertIn("J3=x", reply)
        self.assertIn("[ask J3", reply)

    def test_hallucinated_trade_zone_and_job_are_dropped(self):
        self.gw = Gateway(lambda b: (200, reading(priority=None, trade="carpentry", zone="Atlantis",
                                                   repeat_of="J99"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertIn("J3=-,-,-,-", reply)
        self.assertNotIn("Atlantis", " ".join(commands(reply)))
        self.assertIn("[ask J3", reply)

    def test_gateway_down_is_fast_and_switches_off_after_three_failures(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                 # nothing listens there
        a = self.agent("http://127.0.0.1:%d/v1" % port)
        env, obs = board()
        t0 = time.monotonic()
        reply = a(obs)
        self.assertLess(time.monotonic() - t0, 10)
        self.valid(reply)
        self.assertIn("=x", reply)
        # a history already holding three failures and no answer: the model is off
        cache = {"J1": None, "J2": None, "J3": None}
        self.assertFalse(self.llm_reader.healthy(cache))
        self.assertTrue(self.llm_reader.healthy({"J1": None, "J2": {"prio": 1}}))

    def test_slow_gateway_stays_inside_the_reply_limit(self):
        self.gw = Gateway(lambda b: (200, reading(priority=1), 5.0))
        a = self.agent(self.gw.url)
        self.llm_reader.TICK_BUDGET, self.llm_reader.CALL_TIMEOUT = 1.5, 1.0
        _, obs = board()
        t0 = time.monotonic()
        reply = a(obs)
        self.assertLess(time.monotonic() - t0, 3.0)
        self.valid(reply)
        self.assertIn("J3=x", reply)              # abandoned, counted as a failure, not retried

    def test_restart_mid_shift_reuses_answers_from_the_history(self):
        self.gw = Gateway(lambda b: (200, reading(priority=1, trade="plumbing"), 0))
        a = self.agent(self.gw.url)
        env, obs = board()
        r1 = a(obs)
        n = len(self.gw.requests)
        # the next observation carries r1 (and its notes); a restarted agent must not re-ask
        obs2 = obs + "\n[Player] " + r1 + "\n"
        b = self.agent(self.gw.url)
        r2 = b(obs2)
        self.valid(r2)
        self.assertEqual(len(self.gw.requests), n)
        self.assertNotIn("[ask J3 urgency]", r2)

    def test_every_reply_of_a_shift_is_valid_with_a_flaky_gateway(self):
        calls = {"n": 0}

        def flaky(b):
            calls["n"] += 1
            return [(200, reading(priority=2, trade="IT"), 0), (500, "oops", 0), (200, "{bad", 0)][calls["n"] % 3]
        self.gw = Gateway(flaky)
        a = self.agent(self.gw.url)
        env, obs = board()
        done = False
        while not done:
            r = a(obs)
            self.valid(r)
            done, _ = env.step(r)
            if not done:
                _, obs = env.get_observation()
        self.assertLessEqual(len(self.gw.requests), self.llm_reader.MAX_CALLS)


if __name__ == "__main__":
    unittest.main()

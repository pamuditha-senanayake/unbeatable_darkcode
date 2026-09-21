"""The LLM reader against a fake OpenAI-compatible gateway on 127.0.0.1.

Local only (needs the harness for real boards): `python tests/test_llm_gateway.py`.
Each test points ARENA_LLM_* at a tiny HTTP server that records every request and answers
the way that test needs: well, wrongly, slowly, with junk, or not at all.
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
import arena.games.dispatchdesk.replay as rp  # noqa: E402

UNREADABLE = "Something is not right in the back office, could someone have a look?"


class Gateway:
    """A fake /v1/chat/completions. `answer(path, body) -> (status, text, delay)`."""

    def __init__(self, answer):
        self.requests, self.paths, self.answer = [], [], answer
        gw = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                gw.requests.append(body)
                gw.paths.append(self.path)
                status, text, delay = gw.answer(self.path, body)
                time.sleep(delay)
                if status == 200:
                    reply = {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": text}}],
                             "usage": {"prompt_tokens": 600, "completion_tokens": 120, "total_tokens": 720}}
                else:
                    reply = {"error": {"message": text, "type": "invalid_request_error"}}
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
        self.root = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.url = self.root + "/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def reading(**kw):
    r = {"prio": None, "trade": None, "zone": None, "repeat_of": None, "not_a_job": False,
         "confidence": 0.95, "reason": "test answer"}
    r.update(kw)
    return json.dumps(r)


def truthful(seed):
    """A gateway that reads each ticket right: it looks the job up in the hidden scenario."""
    env, _ = make_env("DispatchDesk-L2")
    env.reset(num_players=1, seed=seed)
    jobs = {j.jid: j for j in rp._unwrap(env).scn.jobs}

    def answer(path, body):
        jid = re.search(r"Message (J\d+) ", body["messages"][1]["content"]).group(1)
        j = jobs[jid]
        return 200, reading(prio=j.priority, trade=j.skill, not_a_job=j.kind == "noise",
                            repeat_of=j.dup_of), 0
    return answer


def board(seed=4, unreadable="J3"):
    env, _ = make_env("DispatchDesk-L2")
    env.reset(num_players=1, seed=seed)
    _, obs = env.get_observation()
    if unreadable:
        obs = re.sub(r'(  %s \d\d:\d\d from [^:]+: )"[^"]*"' % unreadable, r'\1"%s"' % UNREADABLE, obs)
    return env, obs


def commands(reply):
    return re.findall(r"\[[^\]]*\]", reply)


class ModelPath(unittest.TestCase):
    gw = None

    def agent(self, url, key="test-key"):
        os.environ["ARENA_LLM_BASE_URL"], os.environ["ARENA_LLM_API_KEY"] = url, key
        os.environ["ARENA_LLM_MODEL"] = "arena-large"   # the gateway default; the reader picks arena-small
        import llm_reader
        import agent
        importlib.reload(llm_reader)
        importlib.reload(agent)
        self.llm_reader = llm_reader
        return agent.Agent()

    def tearDown(self):
        for k in ("ARENA_LLM_BASE_URL", "ARENA_LLM_API_KEY", "ARENA_LLM_MODEL"):
            os.environ.pop(k, None)
        if self.gw:
            self.gw.close()

    def valid(self, reply):
        cmds = commands(reply)
        # a reply ends in [wait], or is five certain dispatches (guards.FULL_REPLY)
        full = len(cmds) == 5 and all(c.split()[0] in ("[assign", "[unassign") for c in cmds)
        self.assertTrue(cmds and (cmds[-1] == "[wait]" or full), reply)
        self.assertLessEqual(len(cmds), 5, reply)

    def shift(self, a, seed=4):
        """Play a whole L2 shift; every reply must be legal. Returns (replies, score)."""
        env, _ = make_env("DispatchDesk-L2")
        env.reset(num_players=1, seed=seed)
        replies, done = [], False
        while not done:
            _, obs = env.get_observation()
            t0 = time.monotonic()
            r = a(obs)
            self.assertLess(time.monotonic() - t0, 45)
            self.valid(r)
            replies.append(r)
            done, _ = env.step(r)
        rewards, _ = env.close()
        return replies, list(rewards.values())[0]

    # ------------------------------------------------------------------ it is called
    def test_full_shift_makes_requests_once_per_ticket(self):
        self.gw = Gateway(truthful(4))
        a = self.agent(self.gw.url)
        replies, score = self.shift(a)
        self.assertGreater(len(self.gw.requests), 0)
        asked = [re.search(r"Message (J\d+) ", b["messages"][1]["content"]).group(1) for b in self.gw.requests]
        self.assertEqual(len(asked), len(set(asked)), "each ticket is read once")
        body = self.gw.requests[0]
        self.assertEqual(body["model"], "arena-medium")
        self.assertEqual(body["reasoning_effort"], "minimal")
        self.assertLessEqual(body["max_completion_tokens"], 600)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertTrue(any("llm J" in r for r in replies))
        print("\n  full shift: %d requests, score %.3f" % (len(self.gw.requests), score))

    def test_base_url_without_v1_is_retried_with_v1(self):
        self.gw = Gateway(lambda path, body: (200, reading(prio=1, trade="plumbing"), 0)
                          if path.startswith("/v1/") else (404, "not found", 0))
        a = self.agent(self.gw.root)                   # no /v1
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertIn("/v1/chat/completions", self.gw.paths)
        self.assertIn("J3=1,plumbing", reply)

    def test_deepseek_style_gateway_steps_down_the_ladder(self):
        # refuses reasoning_effort (as DeepSeek does); accepts max_tokens and JSON mode
        self.gw = Gateway(lambda path, body: (400, "unknown parameter reasoning_effort", 0)
                          if "reasoning_effort" in body else (200, reading(prio=1, trade="plumbing"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertIn("J3=1,plumbing", reply)
        ok = [b for b in self.gw.requests if "reasoning_effort" not in b]
        self.assertTrue(ok and "max_tokens" in ok[0] and "response_format" in ok[0])

    def test_full_level3_shift_makes_requests(self):
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        import l3_shadow
        l3_shadow.register()
        self.gw = Gateway(lambda path, body: (200, reading(), 0))
        a = self.agent(self.gw.url)
        env, _ = make_env("DispatchDesk-L3")
        env.reset(num_players=1, seed=7)
        done = False
        while not done:
            _, obs = env.get_observation()
            r = a(obs)
            self.valid(r)
            done, _ = env.step(r)
        self.assertGreater(len(self.gw.requests), 40)
        print("\n  full L3 shift: %d requests" % len(self.gw.requests))

    def test_refused_parameter_is_retried_without_it(self):
        self.gw = Gateway(lambda path, body: (400, "response_format not supported", 0)
                          if "response_format" in body else (200, reading(prio=1, trade="plumbing"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertIn("J3=1,plumbing", reply)

    def test_no_gateway_configured_means_rules_only(self):
        os.environ.pop("ARENA_LLM_BASE_URL", None)
        import llm_reader
        import agent
        importlib.reload(llm_reader)
        importlib.reload(agent)
        _, obs = board()
        reply = agent.Agent()(obs)
        self.valid(reply)
        self.assertNotIn("llm ", reply)

    # ------------------------------------------------------------------ failures fall back
    def test_gateway_down_whole_shift_is_legal_and_switches_off(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                      # nothing listens there
        a = self.agent("http://127.0.0.1:%d/v1" % port)
        replies, _ = self.shift(a)
        notes = " ".join(replies)
        self.assertIn("=x:APIConnectionError", notes)
        # the one diagnostic the replay shows, once per shift
        self.assertEqual(notes.count("[llm APIConnectionError]"), 1)
        self.assertLessEqual(notes.count("=x:"), self.llm_reader.MAX_FAILURES + 3)   # then it stops

    def test_slow_gateway_stays_inside_the_reply_limit(self):
        self.gw = Gateway(lambda path, body: (200, reading(prio=1), 5.0))
        a = self.agent(self.gw.url)
        self.llm_reader.TICK_BUDGET, self.llm_reader.CALL_TIMEOUT = 1.5, 1.0
        _, obs = board()
        t0 = time.monotonic()
        reply = a(obs)
        self.assertLess(time.monotonic() - t0, 3.0)
        self.valid(reply)
        self.assertRegex(reply, r"J3=x:(budget|APITimeoutError)")

    def test_junk_whole_shift_is_legal(self):
        answers = ["sure! the boiler", "{bad json", reading(prio=7), json.dumps({"prio": 1, "confidence": 0.9,
                                                                                  "extra": "key"})]
        n = {"i": 0}

        def junk(path, body):
            n["i"] += 1
            return 200, answers[n["i"] % len(answers)], 0
        self.gw = Gateway(junk)
        a = self.agent(self.gw.url)
        replies, _ = self.shift(a)
        self.assertGreater(len(self.gw.requests), 0)

    def test_invented_trade_zone_and_job_are_dropped(self):
        self.gw = Gateway(lambda path, body: (200, reading(trade="carpentry", zone="Atlantis", repeat_of="J99"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.valid(reply)
        self.assertIn("J3=-,-,-,-", reply)
        self.assertNotIn("Atlantis", " ".join(commands(reply)))

    def test_restart_mid_shift_reuses_answers_from_the_history(self):
        self.gw = Gateway(lambda path, body: (200, reading(prio=1, trade="plumbing"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        r1 = a(obs)
        n = len(self.gw.requests)
        b = self.agent(self.gw.url)                    # a restarted process
        r2 = b(obs + "\n[Player] " + r1 + "\n")
        self.valid(r2)
        self.assertEqual(len(self.gw.requests), n)

    # ------------------------------------------------------------------ combining the readers
    def test_unreadable_ticket_is_filled_by_a_confident_model(self):
        self.gw = Gateway(lambda path, body: (200, reading(prio=1, trade="plumbing"), 0))
        a = self.agent(self.gw.url)
        _, obs = board()
        reply = a(obs)
        self.assertNotIn("[ask J3 urgency]", reply)
        self.assertNotIn("[ask J3 problem]", reply)

    def test_possible_p1_disagreement_is_never_asked_about(self):
        # seed 4's J1 reads as P3 ("no rush"); the model says P1: a confident model raises it,
        # an unsure one is ignored, and neither costs a question
        for conf, expect_ask in ((0.95, False), (0.5, False)):
            if self.gw:
                self.gw.close()
            self.gw = Gateway(lambda path, body, c=conf: (200, reading(prio=1, trade="plumbing", confidence=c), 0))
            a = self.agent(self.gw.url)
            _, obs = board(unreadable=None)
            reply = a(obs)
            self.valid(reply)
            self.assertEqual("[ask J1 urgency]" in reply, expect_ask, (conf, reply))


if __name__ == "__main__":
    unittest.main()

"""Router tests against a local fake OpenAI-compatible server (no network needed)."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-router-"))
os.environ["IDOS_HOME"] = str(TMP)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import config, llm, providers  # noqa: E402
from core import router as R  # noqa: E402
from core.keystore import KeyStore  # noqa: E402

KEY_A, KEY_B, KEY_C = "gsk_test_key_A_0001", "gsk_test_key_B_0002", "csk-test_key_C_0003"
BASE = ""
SERVER = None
SAVED = {}


def limits(rem_req=900, rem_tok=7000, lim_req=1000, lim_tok=8000, reset_req="2m59.56s", reset_tok="7.66s"):
    return {"x-ratelimit-limit-requests": str(lim_req), "x-ratelimit-remaining-requests": str(rem_req),
            "x-ratelimit-limit-tokens": str(lim_tok), "x-ratelimit-remaining-tokens": str(rem_tok),
            "x-ratelimit-reset-requests": reset_req, "x-ratelimit-reset-tokens": reset_tok}


def sse(chunks):
    return "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def sse_text(text):
    half = len(text) // 2
    return sse([{"model": "m", "choices": [{"delta": {"content": text[:half]}}]},
                {"choices": [{"delta": {"content": text[half:]}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "x_groq": {"usage": {"total_tokens": 42}}}])


def sse_tool(name, arguments):
    cut = len(arguments) // 2
    first = {"index": 0, "id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments[:cut]}}
    return sse([{"choices": [{"delta": {"tool_calls": [first]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[cut:]}}]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}])


class Reply:
    def __init__(self, status=200, body="", headers=None, ctype="text/event-stream", delay=0.0):
        self.status, self.body, self.headers, self.ctype, self.delay = status, body, headers or {}, ctype, delay


def error(status, message, headers=None, code=""):
    body = json.dumps({"error": {"message": message, "type": "invalid_request_error", "code": code}})
    return Reply(status, body, headers, "application/json")


class Fake:
    handler = None
    seen: list = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, reply):
        body = reply.body.encode("utf-8") if isinstance(reply.body, str) else reply.body
        try:
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.ctype)
            for key, value in reply.headers.items():
                self.send_header(key, value)
            if not reply.delay:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not reply.delay:
                self.wfile.write(body)
                return
            for part in body.split(b"\n\n"):
                if part:
                    self.wfile.write(part + b"\n\n")
                    self.wfile.flush()
                    time.sleep(reply.delay)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        is_json = (self.headers.get("Content-Type") or "").startswith("application/json")
        key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        entry = {"provider": self.path.strip("/").split("/")[0], "path": self.path, "key": key,
                 "body": json.loads(raw) if is_json else raw, "ua": self.headers.get("User-Agent")}
        Fake.seen.append(entry)
        self._reply(Fake.handler(entry["provider"], key, entry["body"], self.path))

    def do_GET(self):
        key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if key == "gsk_bad_key_000000":
            self._reply(error(401, "Invalid API Key", code="invalid_api_key"))
        else:
            self._reply(Reply(200, json.dumps({"data": [{"id": "model-b"}, {"id": "model-a"}]}), ctype="application/json"))


def setUpModule():
    global SERVER, BASE
    SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"
    for name in ("groq", "cerebras", "openai"):
        SAVED[name] = providers.PROVIDERS[name]["base"]
        providers.PROVIDERS[name]["base"] = f"{BASE}/{name}/v1"


def tearDownModule():
    for name, base in SAVED.items():
        providers.PROVIDERS[name]["base"] = base
    SERVER.shutdown()


class RouterTest(unittest.TestCase):
    counter = 0

    def setUp(self):
        RouterTest.counter += 1
        base = TMP / f"case{RouterTest.counter}"
        base.mkdir()
        self.settings = config.Settings(base / "settings.json")
        self.settings.set("models.chain", ["groq:openai/gpt-oss-120b", "cerebras:gpt-oss-120b"])
        self.settings.set("models.max_wait_seconds", 3)
        self.keys = KeyStore(base / "keys.json", base / "none.env", environ={})
        for provider, secret in (("groq", KEY_A), ("groq", KEY_B), ("cerebras", KEY_C)):
            self.keys.add(provider, secret)
        self.events = []
        self.router = R.Router(self.settings, self.keys, emit=lambda e, d=None, b=None: self.events.append((e, d)),
                               state_file=base / "state.json")
        Fake.seen = []
        Fake.handler = None

    def chat(self, **kwargs):
        return asyncio.run(self.router.chat([{"role": "user", "content": "привет"}], **kwargs))

    def bucket(self, index=0, model="openai/gpt-oss-120b"):
        return self.router.status()["accounts"][index]["buckets"][model]

    def names(self):
        return [event for event, _ in self.events]

    def test_helpers(self):
        self.assertAlmostEqual(providers.parse_duration("2m59.56s"), 179.56, places=2)
        self.assertAlmostEqual(providers.parse_duration("120ms"), 0.12)
        self.assertEqual(providers.parse_duration("7.66s"), 7.66)
        self.assertAlmostEqual(providers.parse_reset("1700000010000", now=1700000000.0), 10.0)
        self.assertEqual(providers.parse_reset("30", now=0), 30.0)
        self.assertEqual(providers.human_seconds(5), "5 с")
        self.assertEqual(providers.human_seconds(3700), "1 ч 1 мин")
        self.assertEqual(providers.split_spec("openrouter:openai/gpt-oss-120b:free"),
                         ("openrouter", "openai/gpt-oss-120b:free"))
        image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
        self.assertGreater(providers.estimate_tokens(image), 2000)
        params = config.DEFAULTS["models"]["params"]
        self.assertEqual(providers.params_for(params, "groq:openai/gpt-oss-120b")["reasoning_effort"], "low")
        self.assertEqual(providers.params_for(params, "groq:qwen/qwen3.8-27b")["reasoning_effort"], "none")
        self.assertEqual(providers.params_for(params, "openrouter:x"), {})
        self.assertEqual(providers.alias_spec("переключись на 120b"), "groq:openai/gpt-oss-120b")
        self.assertEqual(providers.alias_spec("быструю"), "groq:openai/gpt-oss-20b")

    def test_stream_text_and_limits(self):
        Fake.handler = lambda p, key, body, path: Reply(200, sse_text("Привет, мир!"), limits(rem_req=990))
        pieces = []
        done = self.chat(on_text=pieces.append)
        self.assertEqual(done.text, "Привет, мир!")
        self.assertEqual("".join(pieces), "Привет, мир!")
        self.assertEqual((done.spec, done.account), ("groq:openai/gpt-oss-120b", "Groq · ключ 1"))
        self.assertEqual(done.usage.get("total_tokens"), 42)
        sent = Fake.seen[0]
        self.assertEqual(sent["ua"], f"IDOS/{config.VERSION}")
        self.assertEqual(sent["key"], KEY_A)
        self.assertTrue(sent["body"]["stream"])
        self.assertEqual(sent["body"]["max_completion_tokens"], 1200)
        self.assertEqual(sent["body"]["reasoning_effort"], "low")
        bucket = self.bucket()
        self.assertEqual((bucket["remaining_requests"], bucket["limit_tokens"], bucket["used_today"]), (990, 8000, 1))

    def test_tool_call_stream(self):
        Fake.handler = lambda p, key, body, path: Reply(200, sse_tool("files", '{"action": "desktop"}'), limits())
        done = self.chat(tools=[{"type": "function", "function": {"name": "files", "parameters": {"type": "object"}}}])
        self.assertEqual(done.tool_calls, [{"id": "call_1", "name": "files", "arguments": '{"action": "desktop"}'}])
        message = done.assistant_message()
        self.assertIsNone(message["content"])
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "files")
        self.assertEqual(Fake.seen[0]["body"]["tool_choice"], "auto")

    def test_429_rotates_to_next_key(self):
        def handler(p, key, body, path):
            if key == KEY_A:
                return error(429, "Rate limit reached for model (RPM)", {"retry-after": "30"}, "rate_limit_exceeded")
            return Reply(200, sse_text("ок"), limits())
        Fake.handler = handler
        self.assertEqual(self.chat().account, "Groq · ключ 2")
        self.assertGreaterEqual(self.bucket(0)["cooldown_in"], 25)
        self.assertEqual([s["key"] for s in Fake.seen], [KEY_A, KEY_B])

    def test_proactive_switch_before_limit(self):
        Fake.handler = lambda p, key, body, path: Reply(200, sse_text("ок"), limits(rem_req=5) if key == KEY_A else limits())
        self.assertEqual(self.chat().account, "Groq · ключ 1")
        self.assertEqual(self.chat().account, "Groq · ключ 2")
        switches = [data for event, data in self.events if event == "router.switch"]
        self.assertEqual(switches[-1]["reason"], "смена ключа")

    def test_401_disables_key_and_persists(self):
        def handler(p, key, body, path):
            if key == KEY_A:
                return error(401, "Invalid API Key", code="invalid_api_key")
            return Reply(200, sse_text("ок"), limits())
        Fake.handler = handler
        self.assertEqual(self.chat().account, "Groq · ключ 2")
        first = self.router.status()["accounts"][0]
        self.assertTrue(first["disabled"])
        self.assertIn("401", first["disabled_reason"])
        self.assertIn("router.account_disabled", self.names())
        again = R.Router(self.settings, self.keys, emit=lambda *a: None, state_file=self.router.state_file)
        self.assertTrue(again.status()["accounts"][0]["disabled"])
        self.router.enable(first["id"], True)
        self.assertFalse(self.router.status()["accounts"][0]["disabled"])

    def test_fallback_to_next_model(self):
        def handler(p, key, body, path):
            if p == "groq":
                return error(429, "Rate limit reached on tokens per day (TPD)", {"retry-after": "300"})
            return Reply(200, sse_text("ответ"), limits())
        Fake.handler = handler
        done = self.chat()
        self.assertEqual(done.spec, "cerebras:gpt-oss-120b")
        self.assertEqual(Fake.seen[-1]["key"], KEY_C)
        self.assertEqual(Fake.seen[-1]["body"]["reasoning_effort"], "low")
        # retry-after is respected exactly; no invented 10-minute floor any more
        self.assertGreater(self.bucket(0)["cooldown_in"], 250)
        self.assertLessEqual(self.bucket(0)["cooldown_in"], 301)

    def test_exhausted_fast_with_reasons(self):
        Fake.handler = lambda p, key, body, path: error(429, "Rate limit", {"retry-after": "120"})
        started = time.time()
        with self.assertRaises(R.Exhausted) as ctx:
            self.chat()
        self.assertLess(time.time() - started, 5)
        self.assertIn("Добавьте ключи", str(ctx.exception))
        self.assertIn("Groq · ключ 1", str(ctx.exception))

    def test_short_wait_then_retry(self):
        self.settings.set("models.chain", ["groq:openai/gpt-oss-120b"])
        calls = {"n": 0}

        def handler(p, key, body, path):
            calls["n"] += 1
            if calls["n"] <= 2:
                return error(429, "Rate limit reached (RPM)", {"retry-after": "1"})
            return Reply(200, sse_text("готово"), limits())
        Fake.handler = handler
        self.assertEqual(self.chat().text, "готово")
        self.assertIn("router.wait", self.names())
        self.assertEqual(calls["n"], 3)

    def test_tpm_rotation(self):
        Fake.handler = lambda p, key, body, path: Reply(
            200, sse_text("ок"), limits(rem_tok=100, reset_tok="50s") if key == KEY_A else limits())
        self.assertEqual(self.chat().account, "Groq · ключ 1")
        self.assertEqual(self.chat().account, "Groq · ключ 2")

    def test_400_retries_without_params(self):
        self.settings.set("models.params", {"groq:openai/gpt-oss-*": {"reasoning_effort": "low",
                                                                     "reasoning_format": "hidden"}})

        def handler(p, key, body, path):
            if "reasoning_format" in body:
                return error(400, "property 'reasoning_format' is unsupported")
            return Reply(200, sse_text("ок"), limits())
        Fake.handler = handler
        self.assertEqual(self.chat().text, "ок")
        self.assertIn("reasoning_format", Fake.seen[0]["body"])
        # only the rejected parameter is dropped, the rest stays
        self.assertNotIn("reasoning_format", Fake.seen[1]["body"])
        self.assertIn("reasoning_effort", Fake.seen[1]["body"])
        self.assertEqual(Fake.seen[1]["key"], KEY_A)
        # and it is remembered: the next request does not waste a call on the 400
        count = len(Fake.seen)
        self.assertEqual(self.chat().text, "ок")
        self.assertEqual(len(Fake.seen), count + 1)
        self.assertNotIn("reasoning_format", Fake.seen[-1]["body"])

    def test_tool_call_and_context_errors(self):
        Fake.handler = lambda p, key, body, path: error(400, "Failed to call a function.", code="tool_use_failed")
        with self.assertRaises(R.ToolCallFailed):
            self.chat()
        Fake.handler = lambda p, key, body, path: error(400, "Please reduce the length of the messages.",
                                                        code="context_length_exceeded")
        with self.assertRaises(R.ContextTooLong):
            self.chat()

    def test_413_means_context_too_long(self):
        self.settings.set("models.chain", ["groq:openai/gpt-oss-120b"])
        Fake.handler = lambda p, key, body, path: error(413, "Request too large for model. Limit 8000, Requested 9000")
        with self.assertRaises(R.ContextTooLong):
            self.chat()
        self.assertEqual(len(Fake.seen), 2)
        self.assertEqual(self.router.budget_tokens(), 2500)

    def test_resolve_and_switch_model(self):
        def run(query):
            return asyncio.run(self.router.resolve_model(query))
        self.assertEqual(run("переключись на быструю модель"), "groq:openai/gpt-oss-20b")
        self.assertEqual(run("используй стандартную"), "groq:openai/gpt-oss-120b")
        self.assertEqual(run("смени модель на cerebras:gpt-oss-120b"), "cerebras:gpt-oss-120b")
        self.assertEqual(run("qwen/qwen3.8-27b"), "groq:qwen/qwen3.8-27b")
        with self.assertRaises(R.RouterError) as ctx:
            run("переключись на платную модель")
        self.assertIn("Платная модель ещё не настроена", str(ctx.exception))
        self.assertEqual(run("клод"), "anthropic:claude-sonnet-4-5")
        with self.assertRaises(R.RouterError) as ctx:
            self.router.set_active("anthropic:claude-sonnet-4-5")
        self.assertIn("Нет ключа Anthropic", str(ctx.exception))
        self.keys.add("openai", "sk-proj-test-000000000")
        self.router.invalidate()
        self.assertEqual(run("включи платную модель"), "openai:gpt-5.1")
        self.assertEqual(self.router.set_active("openai:gpt-5.1"), "openai:gpt-5.1")
        self.assertEqual(self.settings.get("models.active"), "openai:gpt-5.1")
        self.assertEqual(self.router.chain_for()[0], "openai:gpt-5.1")
        with self.assertRaises(R.RouterError):
            run("абракадабра")

    def test_cancel_stream(self):
        Fake.handler = lambda p, key, body, path: Reply(200, sse_text("очень длинный ответ " * 5), limits(), delay=0.4)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.time()
        with self.assertRaises(llm.Cancelled):
            self.chat(cancel=cancel)
        self.assertLess(time.time() - started, 1.5)

    def test_transcribe(self):
        Fake.handler = lambda p, key, body, path: Reply(200, json.dumps({"text": " привет мир "}), limits(),
                                                        "application/json")
        self.assertEqual(asyncio.run(self.router.transcribe(b"RIFF0000WAVE", language="ru")), "привет мир")
        self.assertTrue(Fake.seen[0]["path"].endswith("/audio/transcriptions"))
        self.assertIn(b"whisper-large-v3-turbo", Fake.seen[0]["body"])

    def test_keystore_env_and_many(self):
        env = {"GROQ_API_KEYS": "gsk_env_aaaa1111, gsk_env_bbbb2222", "GROQ_API_KEY_3": "gsk_env_cccc3333", "PATH": "/bin"}
        store = KeyStore(TMP / "ks.json", TMP / "ks.env", environ=env)
        (TMP / "ks.env").write_text("# comment\nCEREBRAS_API_KEY='csk-env-dddd4444'\n", encoding="utf-8")
        entries = store.entries()
        self.assertEqual([e["provider"] for e in entries], ["groq", "groq", "groq", "cerebras"])
        self.assertTrue(all(e["id"].startswith("env-") for e in entries))
        self.assertEqual(store.add_many("groq", "gsk_new_eeee5555\ngsk_env_aaaa1111\n"), {"added": 1, "skipped": 1})
        store.set_enabled(entries[0]["id"], False)
        self.assertFalse(store.entries()[0]["enabled"])
        self.assertEqual(KeyStore.mask("gsk_abcdefghijklmnop"), "gsk_…mnop")
        with self.assertRaises(ValueError):
            store.add("nope", "x" * 20)
        file_id = [e for e in store.entries() if e["source"] == "file"][0]["id"]
        self.assertTrue(store.remove(file_id))

    def test_budget_tokens(self):
        self.assertEqual(self.router.budget_tokens(), 5000)  # capped by models.max_input_tokens
        self.settings.set("models.max_input_tokens", 0)
        self.assertEqual(self.router.budget_tokens(), 6400)
        Fake.handler = lambda p, key, body, path: Reply(200, sse_text("ок"), limits(lim_tok=12000))
        self.chat()
        self.assertEqual(self.router.budget_tokens(), 12000 - 1200 - 400)
        self.settings.set("models.active", "cerebras:gpt-oss-120b")
        self.assertEqual(self.router.budget_tokens(), 24000)

    def test_network_error_falls_back(self):
        providers.PROVIDERS["groq"]["base"] = "http://127.0.0.1:9/groq/v1"
        try:
            Fake.handler = lambda p, key, body, path: Reply(200, sse_text("из cerebras"), limits())
            self.assertEqual(self.chat().spec, "cerebras:gpt-oss-120b")
            self.assertIn("нет связи", self.bucket(0)["last_error"])
        finally:
            providers.PROVIDERS["groq"]["base"] = f"{BASE}/groq/v1"

    def test_non_stream_vision_chain(self):
        body = json.dumps({"model": "m", "choices": [{"message": {"content": "картинка"}, "finish_reason": "stop"}]})
        Fake.handler = lambda p, key, b, path: Reply(200, body, limits(), "application/json")
        done = asyncio.run(self.router.chat([{"role": "user", "content": "x"}], stream=False, purpose="vision"))
        self.assertEqual((done.text, done.spec), ("картинка", "groq:qwen/qwen3.8-27b"))
        self.assertNotIn("stream", Fake.seen[0]["body"])
        self.assertEqual(Fake.seen[0]["body"]["reasoning_effort"], "none")


    def test_qwen_only_reads_images(self):
        qwen = "groq:qwen/qwen3.8-27b"
        self.assertTrue(self.router.vision_only(qwen))
        self.assertTrue(self.router.vision_only("cerebras:qwen-3.8-27b"))
        self.assertFalse(self.router.vision_only("groq:openai/gpt-oss-120b"))
        self.settings.set("models.active", qwen)
        self.settings.set("models.fast", qwen)
        self.settings.set("models.chain", [qwen, "groq:openai/gpt-oss-120b", "cerebras:qwen-3.8-27b"])
        for purpose in ("agent", "fast", "summary"):
            chain = self.router.chain_for(purpose)
            self.assertTrue(chain, purpose)
            self.assertFalse([s for s in chain if "qwen" in s], (purpose, chain))
        self.assertEqual(self.router.chain_for("vision")[0], qwen)

    def test_explicit_qwen_chain_is_refused_for_text(self):
        with self.assertRaises(R.Exhausted):
            self.chat(chain=["groq:qwen/qwen3.8-27b"])
        self.assertEqual(Fake.seen, [])

    def test_switch_to_qwen_sets_vision_model(self):
        self.settings.set("models.active", "groq:openai/gpt-oss-120b")
        self.settings.set("models.vision", "gemini:gemini-2.5-flash")
        self.router.set_active("groq:qwen/qwen3.8-27b")
        self.assertEqual(self.settings.get("models.active"), "groq:openai/gpt-oss-120b")
        self.assertEqual(self.settings.get("models.vision"), "groq:qwen/qwen3.8-27b")
        self.router.set_active("groq:openai/gpt-oss-20b")
        self.assertEqual(self.settings.get("models.active"), "groq:openai/gpt-oss-20b")

    def test_default_chain_has_no_qwen(self):
        chain = config.DEFAULTS["models"]["chain"] if hasattr(config, "DEFAULTS") else []
        self.assertFalse([s for s in chain if "qwen" in s])


    def test_pair_only_no_auto_models(self):
        self.settings.set("models.chain", list(config.DEFAULTS["models"]["chain"]))
        self.settings.set("models.fast", config.DEFAULTS["models"]["fast"])
        self.keys.add("gemini", "AIza_test_key_0004")
        self.router.invalidate()
        self.assertEqual(self.router.chain_for("agent"), ["groq:openai/gpt-oss-120b"])
        self.assertEqual(self.router.chain_for("fast"), ["groq:openai/gpt-oss-120b"])
        self.assertEqual(self.router.chain_for("vision"), ["groq:qwen/qwen3.8-27b"])
        self.settings.set("models.vision_chain", ["gemini:gemini-2.5-flash"])
        self.assertEqual(self.router.chain_for("vision"), ["groq:qwen/qwen3.8-27b", "gemini:gemini-2.5-flash"])

    def test_old_fallback_chain_is_migrated_once(self):
        base = TMP / f"migrate{RouterTest.counter}"
        base.mkdir()
        old = {"models": {"chain": ["groq:openai/gpt-oss-120b", "cerebras:gpt-oss-120b", "groq:qwen/qwen3.8-27b",
                                    "groq:openai/gpt-oss-20b", "openrouter:openai/gpt-oss-120b:free"],
                          "fast": "groq:openai/gpt-oss-20b", "active": "groq:openai/gpt-oss-120b"}}
        (base / "settings.json").write_text(json.dumps(old), encoding="utf-8")
        settings = config.Settings(base / "settings.json")
        self.assertEqual(settings.get("models.chain"), ["groq:openai/gpt-oss-120b",
                                                        "anymodel:am/nemotron-3-ultra-550b-a55b"])
        self.assertEqual(settings.get("models.fast"), "groq:openai/gpt-oss-120b")
        saved = json.loads((base / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["models"]["defaults_rev"], 5)
        settings.set("models.chain", ["groq:openai/gpt-oss-120b", "cerebras:gpt-oss-120b"])
        again = config.Settings(base / "settings.json")
        self.assertEqual(again.get("models.chain"), ["groq:openai/gpt-oss-120b", "cerebras:gpt-oss-120b"])

    def test_model_switch_only_on_user_initiative(self):
        from core.agent import user_asked_model
        self.assertTrue(user_asked_model("переключись на платную модель"))
        self.assertTrue(user_asked_model("включи claude"))
        self.assertTrue(user_asked_model("используй gemini"))
        self.assertFalse(user_asked_model("открой блокнот и напиши список покупок"))
        self.assertFalse(user_asked_model("какая погода завтра"))


    def test_tpd_429_uses_exact_wait_and_explains(self):
        msg = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` "
               "on tokens per day (TPD): Limit 200000, Used 196543, Requested 7012. Please try again in 9m36.48s. "
               "Need more tokens? Upgrade to Dev Tier today")
        Fake.handler = lambda p, key, b, path: error(429, msg, code="rate_limit_exceeded")
        with self.assertRaises(R.Exhausted) as ctx:
            self.chat()
        text = str(ctx.exception)
        self.assertIn("дневной лимит токенов", text)
        self.assertIn("196 543 из 200 000", text)
        self.assertNotIn("Rate limit reached", text)
        self.assertIn("скользящие 24 часа", text)
        self.assertGreater(ctx.exception.retry_at, time.time() + 500)
        self.assertLess(ctx.exception.retry_at, time.time() + 600)
        labels = [line.split(": ", 1)[0] for line in text.splitlines() if line.startswith("• ")]
        self.assertEqual(len(labels), len(set(labels)))
        bucket = self.bucket(0)
        self.assertLessEqual(bucket["cooldown_in"], 577)

    def test_rate_limit_parser_kinds(self):
        info = providers.parse_rate_limit("Rate limit reached ... on requests per minute (RPM): Limit 30, Used 30, "
                                          "Requested 1. Please try again in 1.5s.")
        self.assertEqual((info["kind"], info["limit"], info["wait"]), ("RPM", 30, 1.5))
        self.assertEqual(providers.parse_rate_limit("slow down")["kind"], "")
        self.assertIn("лимит запросов в минуту", providers.describe_rate_limit(info))

    def test_input_budget_cap(self):
        self.settings.set("models.max_input_tokens", 3000)
        self.assertLessEqual(self.router.budget_tokens(), 3000)


if __name__ == "__main__":
    unittest.main()

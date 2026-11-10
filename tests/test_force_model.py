import unittest

from core import router as rmod


class FakeSettings:
    def __init__(self, data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class R:
    """Only the router methods under test, bound to a minimal fake."""
    forced = rmod.Router.forced
    set_force = rmod.Router.set_force
    choices = rmod.Router.choices
    chain_for = rmod.Router.chain_for

    def __init__(self, settings, keys=("groq", "anymodel")):
        self.settings = settings
        self.keys = set(keys)
        self.events = []

    def normalize(self, spec):
        return spec

    def has_provider(self, p):
        return p in self.keys

    def vision_only(self, spec):
        return "qwen" in spec

    def emit(self, *a):
        self.events.append(a)

    def status(self):
        return {}


BASE = {"models.active": "groq:openai/gpt-oss-120b", "models.fast": "groq:openai/gpt-oss-120b",
        "models.chain": ["anymodel:am/nemotron-3-ultra-550b-a55b"], "models.vision": "groq:qwen/qwen3.8-27b"}


@unittest.skipUnless("groq" in rmod.PROVIDERS and "anymodel" in rmod.PROVIDERS, "providers")
class ForceModelTest(unittest.TestCase):
    def test_auto_uses_chain(self):
        r = R(FakeSettings(BASE))
        self.assertEqual(r.chain_for("agent"), ["groq:openai/gpt-oss-120b", "anymodel:am/nemotron-3-ultra-550b-a55b"])

    def test_forced_only_that_model(self):
        r = R(FakeSettings(BASE))
        self.assertEqual(r.set_force("anymodel:am/nemotron-3-ultra-550b-a55b"), "anymodel:am/nemotron-3-ultra-550b-a55b")
        self.assertEqual(r.chain_for("agent"), ["anymodel:am/nemotron-3-ultra-550b-a55b"])
        self.assertEqual(r.chain_for("fast"), ["anymodel:am/nemotron-3-ultra-550b-a55b"])
        self.assertEqual(r.chain_for("vision"), ["groq:qwen/qwen3.8-27b"])  # vision is not affected
        self.assertEqual(len(r.chain_for("agent", auto=True)), 2)
        self.assertEqual(r.set_force("auto"), "")
        self.assertEqual(len(r.chain_for("agent")), 2)

    def test_rejects_vision_and_missing_key(self):
        r = R(FakeSettings(BASE), keys=("groq",))
        with self.assertRaises(rmod.RouterError):
            r.set_force("groq:qwen/qwen3.8-27b")
        with self.assertRaises(rmod.RouterError):
            r.set_force("anymodel:am/nemotron-3-ultra-550b-a55b")
        # a pinned model whose key was removed falls back to Auto instead of breaking the chat
        r.settings.set("models.force", "anymodel:am/nemotron-3-ultra-550b-a55b")
        self.assertEqual(r.forced(), "")

    def test_choices_only_user_models(self):
        r = R(FakeSettings(BASE), keys=("groq",))
        specs = [c["spec"] for c in r.choices()]
        self.assertEqual(specs, ["groq:openai/gpt-oss-120b", "anymodel:am/nemotron-3-ultra-550b-a55b"])
        self.assertFalse(r.choices()[1]["ready"])


if __name__ == "__main__":
    unittest.main()

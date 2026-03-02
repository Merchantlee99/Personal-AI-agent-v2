import unittest

from app.agents import AGENT_REGISTRY, normalize_agent_id


class AgentRegistryTests(unittest.TestCase):
    def test_canonical_ids(self) -> None:
        self.assertEqual(set(AGENT_REGISTRY.keys()), {"minerva", "clio", "hermes"})

    def test_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_agent_id("ace"), "minerva")
        self.assertEqual(normalize_agent_id("owl"), "clio")
        self.assertEqual(normalize_agent_id("dolphin"), "hermes")

    def test_unknown_alias_returns_none(self) -> None:
        self.assertIsNone(normalize_agent_id("unknown"))

    def test_canonical_values_return_as_is(self) -> None:
        self.assertEqual(normalize_agent_id("minerva"), "minerva")
        self.assertEqual(normalize_agent_id("clio"), "clio")
        self.assertEqual(normalize_agent_id("hermes"), "hermes")


if __name__ == "__main__":
    unittest.main()

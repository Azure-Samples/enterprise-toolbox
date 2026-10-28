from __future__ import annotations

import unittest

from select_changed_paths import select_groups


class SelectChangedPathsTests(unittest.TestCase):
    def test_selects_workflow_validation(self) -> None:
        selected = select_groups([".github/workflows/deploy-agent.yml"])
        self.assertTrue(selected["workflows"])
        self.assertFalse(selected["foundation"])
        self.assertFalse(selected["agent"])
        self.assertTrue(selected["configuration"])

    def test_selects_foundation_for_infrastructure_and_knowledge_docs(self) -> None:
        for path in (
            "foundation/infra/main.bicep",
            "services/foundry-iq-docs/docs/safety.md",
        ):
            with self.subTest(path=path):
                selected = select_groups([path])
                self.assertTrue(selected["foundation"])
                self.assertFalse(selected["agent"])
                self.assertTrue(selected["configuration"])

    def test_selects_agent_for_all_build_inputs(self) -> None:
        for path in (
            "agent/azure.yaml",
            "src/fibey/agent/hosted.py",
            ".dockerignore",
            "Dockerfile.agent",
            "pyproject.toml",
            "uv.lock",
        ):
            with self.subTest(path=path):
                selected = select_groups([path])
                self.assertTrue(selected["agent"])
                self.assertFalse(selected["foundation"])
                self.assertTrue(selected["configuration"])

    def test_test_changes_select_every_validation_group(self) -> None:
        selected = select_groups(["tests/deployment/test_load_foundation_context.py"])
        self.assertTrue(selected["workflows"])
        self.assertTrue(selected["foundation"])
        self.assertTrue(selected["agent"])
        self.assertTrue(selected["configuration"])

    def test_unrelated_changes_still_produce_a_stable_successful_check(self) -> None:
        selected = select_groups(["ui/src/App.tsx", "docs/deployment.md"])
        self.assertFalse(any(selected.values()))


if __name__ == "__main__":
    unittest.main()

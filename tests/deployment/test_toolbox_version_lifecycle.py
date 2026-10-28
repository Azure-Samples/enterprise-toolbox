from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "foundation" / "scripts" / "toolbox-version-lifecycle.py"
SPEC = importlib.util.spec_from_file_location("toolbox_version_lifecycle", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)


def version_list(*versions: tuple[str, str], default: str | None = None) -> str:
    payload: dict[str, object] = {
        "versions": [
            {"id": version_id, "version": version}
            for version_id, version in versions
        ]
    }
    if default is not None:
        payload["default_version"] = default
    return json.dumps(payload)


EXISTING_TOOLBOX = json.dumps({"toolboxes": [{"name": "fibey-toolbox"}]})


class ToolboxVersionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patch_root = patch.object(lifecycle, "ROOT", Path(temporary.name))
        patch_root.start()
        self.addCleanup(patch_root.stop)
        patch_environment = patch.dict(os.environ, {"AZURE_ENV_NAME": "test-ci"})
        patch_environment.start()
        self.addCleanup(patch_environment.stop)

    def test_promotes_only_the_new_version_and_verifies_the_default(self) -> None:
        initial = version_list(("id-1", "1"))
        with patch.object(
            lifecycle,
            "_run_azd",
            side_effect=[
                EXISTING_TOOLBOX,
                initial,
                version_list(("id-1", "1"), ("id-2", "2")),
                "",
                version_list(("id-1", "1"), ("id-2", "2"), default="2"),
            ],
        ) as run_azd:
            lifecycle.record_baseline()
            lifecycle.promote_deployed_version()

        self.assertIn(
            unittest.mock.call(
                "ai",
                "toolbox",
                "publish",
                "fibey-toolbox",
                "2",
                "--no-prompt",
            ),
            run_azd.call_args_list,
        )
        self.assertFalse(lifecycle._baseline_path().exists())

    def test_refuses_to_promote_when_multiple_versions_are_new(self) -> None:
        with patch.object(
            lifecycle,
            "_run_azd",
            side_effect=[
                EXISTING_TOOLBOX,
                version_list(("id-1", "1")),
                version_list(("id-1", "1"), ("id-2", "2"), ("id-3", "3")),
            ],
        ) as run_azd:
            lifecycle.record_baseline()
            with self.assertRaisesRegex(RuntimeError, "found 2"):
                lifecycle.promote_deployed_version()

        self.assertFalse(
            any("publish" in call.args for call in run_azd.call_args_list)
        )
        self.assertTrue(lifecycle._baseline_path().exists())

    def test_keeps_baseline_when_published_version_is_not_default(self) -> None:
        with patch.object(
            lifecycle,
            "_run_azd",
            side_effect=[
                EXISTING_TOOLBOX,
                version_list(("id-1", "1")),
                version_list(("id-1", "1"), ("id-2", "2")),
                "",
                version_list(("id-1", "1"), ("id-2", "2"), default="1"),
            ],
        ):
            lifecycle.record_baseline()
            with self.assertRaisesRegex(RuntimeError, "is not the default"):
                lifecycle.promote_deployed_version()

        self.assertTrue(lifecycle._baseline_path().exists())

    def test_first_publication_records_empty_baseline_and_promotes_version_one(self) -> None:
        with patch.object(
            lifecycle,
            "_run_azd",
            side_effect=[
                '{"toolboxes":[]}',
                version_list(("id-1", "1")),
                "",
                version_list(("id-1", "1"), default="1"),
            ],
        ) as run_azd:
            lifecycle.record_baseline()
            baseline = json.loads(lifecycle._baseline_path().read_text(encoding="utf-8"))
            self.assertEqual(baseline["version_ids"], [])
            lifecycle.promote_deployed_version()
        self.assertEqual(run_azd.call_count, 4)
        self.assertFalse(lifecycle._baseline_path().exists())

    def test_list_errors_never_become_an_empty_baseline(self) -> None:
        for error in ("Forbidden", "Unauthorized", "connection failed", "not found"):
            with self.subTest(error=error), patch.object(
                lifecycle, "_run_azd", side_effect=RuntimeError(error),
            ):
                with self.assertRaisesRegex(RuntimeError, error):
                    lifecycle.record_baseline()
                self.assertFalse(lifecycle._baseline_path().exists())

    def test_invalid_toolbox_lists_are_rejected(self) -> None:
        for payload in ('{}', '{"toolboxes":[{}]}', 'not json', '{broken}', '{"toolboxes":null}'):
            with self.subTest(payload=payload), patch.object(
                lifecycle, "_run_azd", return_value=payload,
            ):
                with self.assertRaises(RuntimeError):
                    lifecycle.record_baseline()
                self.assertFalse(lifecycle._baseline_path().exists())


if __name__ == "__main__":
    unittest.main()

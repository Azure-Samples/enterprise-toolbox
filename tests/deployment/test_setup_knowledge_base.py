from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "foundation" / "scripts" / "setup-knowledge-base.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(f"#!/usr/bin/env python3\n{source}", encoding="utf-8")
    path.chmod(0o755)


class KnowledgeBaseHookTests(unittest.TestCase):
    def _run_hook(self, status_responses: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        bin_directory = root / "bin"
        bin_directory.mkdir()
        curl_log = root / "curl.jsonl"
        status_count = root / "status-count"

        _write_executable(
            bin_directory / "az",
            """
import sys

arguments = sys.argv[1:]
if arguments[:3] == ["storage", "account", "show"]:
    print("/subscriptions/test/resourceGroups/rg-fibey-jf-1026/providers/Microsoft.Storage/storageAccounts/fibeystorage")
elif arguments[:3] == ["storage", "blob", "upload-batch"]:
    pass
else:
    print(f"Unexpected az command: {arguments}", file=sys.stderr)
    raise SystemExit(2)
""",
        )
        _write_executable(
            bin_directory / "azd",
            """
import sys

print(f"Unexpected azd command: {sys.argv[1:]}", file=sys.stderr)
raise SystemExit(2)
""",
        )
        _write_executable(
            bin_directory / "curl",
            """
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
with Path(os.environ["CURL_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + chr(10))
status_url = next((arg for arg in arguments if "search.status" in arg), None)
if status_url:
    counter_path = Path(os.environ["CURL_STATUS_COUNT"])
    count = int(counter_path.read_text() if counter_path.exists() else "0")
    responses = json.loads(os.environ["INDEXER_STATUS_RESPONSES"])
    print(json.dumps(responses[count]))
    counter_path.write_text(str(count + 1))
""",
        )
        _write_executable(bin_directory / "sleep", "raise SystemExit(0)\n")

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{bin_directory}{os.pathsep}{environment['PATH']}",
                "CURL_LOG": str(curl_log),
                "CURL_STATUS_COUNT": str(status_count),
                "INDEXER_STATUS_RESPONSES": json.dumps(status_responses),
                "GITHUB_ACTIONS": "true",
                "AZURE_RESOURCE_GROUP": "rg-fibey-jf-1026",
                "STORAGE_ACCOUNT": "fibeystorage",
                "SEARCH_SERVICE": "fibey-jf-1026-search",
                "AZURE_SEARCH_INDEX": "foundry-iq-docs-index",
                "KB_NAME": "fibey-field-ops-kb",
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
                "FOUNDRY_MODEL": "gpt-4.1-mini",
                "AZURE_SEARCH_ADMIN_KEY": "test-search-admin-key",
            }
        )
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    @staticmethod
    def _status(
        *,
        overall: str = "running",
        result: str = "success",
        start: str = "2026-05-01T00:00:00Z",
        end: str = "2026-05-01T00:01:00Z",
        processed: int = 3,
        failed: int = 0,
    ) -> dict[str, object]:
        return {
            "status": overall,
            "lastResult": {
                "status": result,
                "startTime": start,
                "endTime": end,
                "itemsProcessed": processed,
                "itemsFailed": failed,
            },
        }

    def test_waits_for_current_indexer_run_before_creating_knowledge_base(self) -> None:
        result = self._run_hook(
            [
                self._status(),
                self._status(
                    start="2026-05-10T00:00:00Z",
                    end="2026-05-10T00:01:00Z",
                ),
            ]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Indexer succeeded: processed 3 item(s), failed 0.", result.stdout)
        self.assertIn("=== Creating knowledge source ===", result.stdout)
        self.assertIn("=== Creating knowledge base ===", result.stdout)
        self.assertIn("=== Knowledge base ready ===", result.stdout)

    def test_stops_before_knowledge_base_creation_when_indexer_fails(self) -> None:
        result = self._run_hook(
            [
                self._status(),
                self._status(overall="error", result="transientFailure"),
            ]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The indexer entered an error state", result.stdout)
        self.assertNotIn("=== Creating knowledge source ===", result.stdout)
        self.assertNotIn("=== Creating knowledge base ===", result.stdout)


if __name__ == "__main__":
    unittest.main()

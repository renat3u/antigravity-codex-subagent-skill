#!/usr/bin/env python3
"""Offline unit tests for codex_subagent.py."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "skills" / "codex-subagent" / "scripts" / "codex_subagent.py"

spec = importlib.util.spec_from_file_location("codex_subagent", MODULE_PATH)
assert spec and spec.loader
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class CodexSubagentUnitTests(unittest.TestCase):
    def test_model_aliases(self) -> None:
        self.assertEqual(cs.resolve_model("sol"), "gpt-6.2-sol")
        self.assertEqual(cs.resolve_model("ASTRA"), "gpt-6.2-astra")
        self.assertEqual(cs.resolve_model("terra"), "gpt-6.1-terra")
        self.assertEqual(cs.resolve_model("reserve"), "gpt-reserve")
        self.assertEqual(cs.resolve_model("gpt-future-7.0"), "gpt-future-7.0")

    def test_effort_aliases_and_auto_grading(self) -> None:
        self.assertEqual(cs.resolve_effort("auto", "read-only"), "medium")
        self.assertEqual(cs.resolve_effort("auto", "workspace-write"), "high")
        self.assertEqual(cs.resolve_effort("超高", "workspace-write"), "xhigh")
        self.assertEqual(cs.resolve_effort("拉满", "workspace-write"), "ultra")
        self.assertEqual(cs.resolve_effort("低", "read-only"), "low")

    def test_mode_and_ephemeral_policy(self) -> None:
        ro = cs.resolve_mode("read")
        rw = cs.resolve_mode("write")
        full = cs.resolve_mode("full")
        self.assertEqual(ro, "read-only")
        self.assertEqual(rw, "workspace-write")
        self.assertEqual(full, "danger-full-access")

        # Smart session retention: read-only defaults ephemeral, write defaults resumable
        self.assertTrue(cs.should_be_ephemeral(ro, force_ephemeral=False, force_keep=False))
        self.assertFalse(cs.should_be_ephemeral(ro, force_ephemeral=False, force_keep=True))
        self.assertFalse(cs.should_be_ephemeral(rw, force_ephemeral=False, force_keep=False))
        self.assertTrue(cs.should_be_ephemeral(rw, force_ephemeral=True, force_keep=False))

    def test_parse_jsonl_events(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "01a20b92-test-uuid"},
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "command_execution",
                    "command": "pytest -q",
                    "exit_code": 0,
                    "aggregated_output": "3 passed\n",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "false",
                    "exit_code": 1,
                    "aggregated_output": "boom",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "agent_message",
                    "text": "STATUS: DONE",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 800,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 120,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "events.jsonl"
            p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
            stats = cs.parse_jsonl_events(p)

        self.assertEqual(stats["thread_id"], "01a20b92-test-uuid")
        self.assertEqual(stats["commands_total"], 2)
        self.assertEqual(stats["commands_failed"], 1)
        self.assertEqual(stats["last_agent_message"], "STATUS: DONE")
        self.assertEqual(stats["usage"]["input_tokens"], 1000)


if __name__ == "__main__":
    unittest.main()

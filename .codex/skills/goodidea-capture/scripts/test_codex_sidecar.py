#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("codex_sidecar.py")


def record(ordinal: int, record_type: str, payload: dict) -> dict:
    return {
        "ordinal": ordinal,
        "timestamp": f"2026-09-29T14:00:{ordinal:02d}.000Z",
        "type": record_type,
        "payload": payload,
    }


def visible(ordinal: int, item_type: str, text: str, phase: str | None = None) -> dict:
    item = {"type": item_type, "id": f"item-{ordinal}", "content": [{"type": "text", "text": text}]}
    if phase:
        item["phase"] = phase
    return record(ordinal, "event_msg", {"type": "item_completed", "item": item})


class SidecarCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.thread_id = "01test-thread"
        self.session = self.root / f"rollout-{self.thread_id}.jsonl"
        self.output = self.root / "数据/记录/闪念/原始思考记录/test.md"
        self.state_dir = self.root / ".goodidea/sidecar"
        self.records = [
            record(1, "event_msg", {"type": "task_started", "turn_id": "turn-1"}),
            record(2, "response_item", {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "secret developer"}]}),
            record(3, "response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "injected environment"}]}),
            visible(4, "UserMessage", "真实用户消息"),
            record(5, "response_item", {"type": "reasoning", "summary": "hidden reasoning"}),
            visible(6, "AgentMessage", "可见中间回应", "commentary"),
            record(7, "response_item", {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "可见中间回应"}]}),
            record(8, "response_item", {"type": "custom_tool_call", "name": "tool"}),
            visible(9, "AgentMessage", "可见最终回应", "final_answer"),
        ]
        self.write_records()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_records(self, partial: bytes | None = None) -> None:
        with self.session.open("wb") as handle:
            for item in self.records:
                handle.write(json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n")
            if partial:
                handle.write(partial)

    def run_cli(self, *args: str, expect: int = 0, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["CODEX_THREAD_ID"] = self.thread_id
        completed = subprocess.run(
            ["python3", str(SCRIPT), *args],
            cwd=cwd or self.root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, expect, completed.stderr)
        return completed

    def start(self) -> None:
        self.run_cli(
            "start",
            "--output", str(self.output),
            "--session-file", str(self.session),
            "--state-dir", str(self.state_dir),
            "--no-watch",
        )

    def test_default_paths_work_without_project_documents(self) -> None:
        relative_output = "数据/记录/闪念/原始思考记录/test.md"
        self.run_cli("start", "--output", relative_output, "--session-file", str(self.session), "--no-watch")
        self.assertIn("真实用户消息", self.output.read_text(encoding="utf-8"))
        state_file = self.state_dir / "captures" / f"{self.thread_id}.json"
        self.assertEqual(Path(json.loads(state_file.read_text())["output_file"]), self.output.resolve())

        nested = self.root / "数据/记录/闪念"
        self.records.append(visible(10, "UserMessage", "子目录中的同步"))
        self.write_records()
        self.run_cli("sync", cwd=nested)
        self.assertIn("子目录中的同步", self.output.read_text(encoding="utf-8"))
        self.run_cli("close", "--now", cwd=nested)

    def test_skill_location_identifies_root_before_data_exists(self) -> None:
        skill_file = self.root / ".codex/skills/goodidea-capture/SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("standalone skill\n", encoding="utf-8")
        self.run_cli("start", "--output", "数据/记录/闪念/原始思考记录/test.md", "--session-file", str(self.session), "--no-watch", cwd=skill_file.parent)
        self.assertIn("真实用户消息", self.output.read_text(encoding="utf-8"))
        self.run_cli("status", cwd=skill_file.parent)
        self.run_cli("close", "--now", cwd=skill_file.parent)

    def test_only_visible_completed_messages_are_recorded(self) -> None:
        self.start()
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("真实用户消息", text)
        self.assertIn("可见中间回应", text)
        self.assertIn("可见最终回应", text)
        self.assertNotIn("secret developer", text)
        self.assertNotIn("injected environment", text)
        self.assertNotIn("hidden reasoning", text)
        self.assertEqual(text.count("可见中间回应"), 1)

    def test_sync_is_idempotent_and_retries_partial_last_line(self) -> None:
        self.start()
        complete = visible(10, "UserMessage", "后续补充")
        encoded = json.dumps(complete, ensure_ascii=False).encode("utf-8")
        self.write_records(partial=encoded[:20])
        self.run_cli("sync", "--state-dir", str(self.state_dir))
        self.assertNotIn("后续补充", self.output.read_text(encoding="utf-8"))

        self.records.append(complete)
        self.write_records()
        self.run_cli("sync", "--state-dir", str(self.state_dir))
        self.run_cli("sync", "--state-dir", str(self.state_dir))
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual(text.count("后续补充"), 1)

    def test_close_prevents_later_messages_from_being_written(self) -> None:
        self.start()
        self.run_cli("close", "--state-dir", str(self.state_dir), "--now")
        self.records.append(visible(10, "UserMessage", "关闭以后"))
        self.write_records()
        result = self.run_cli("sync", "--state-dir", str(self.state_dir))
        self.assertIn('"status": "closed"', result.stdout)
        self.assertNotIn("关闭以后", self.output.read_text(encoding="utf-8"))

    def test_scheduled_close_keeps_current_turn_final_reply(self) -> None:
        self.start()
        result = self.run_cli("close", "--state-dir", str(self.state_dir))
        self.assertIn('"status": "closing"', result.stdout)
        self.records.extend(
            [
                visible(10, "AgentMessage", "关闭前最后回应", "final_answer"),
                record(11, "event_msg", {"type": "task_complete", "turn_id": "turn-1"}),
            ]
        )
        self.write_records()
        result = self.run_cli("sync", "--state-dir", str(self.state_dir))
        self.assertIn('"status": "closed"', result.stdout)
        self.assertIn("关闭前最后回应", self.output.read_text(encoding="utf-8"))

    def test_later_messages_stay_before_destination_section(self) -> None:
        self.start()
        with self.output.open("a", encoding="utf-8") as handle:
            handle.write("\n## 去向\n\n- 已形成闪念。\n")
        self.records.append(visible(10, "AgentMessage", "最后一条可见回复", "final_answer"))
        self.write_records()
        self.run_cli("sync", "--state-dir", str(self.state_dir))
        text = self.output.read_text(encoding="utf-8")
        self.assertLess(text.index("最后一条可见回复"), text.index("## 去向"))
        self.assertIn("- 已形成闪念。", text)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Record visible messages from one Codex thread into a Good Idea Markdown file."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator


VERSION = 1
USER_LABEL = "松海"
AGENT_LABEL = "Agent"
MARKER_PREFIX = "<!-- goodidea-sidecar:event="
MESSAGES_END = "<!-- goodidea-sidecar:messages-end -->"


class SidecarError(RuntimeError):
    pass


def project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".codex/skills/goodidea-capture/SKILL.md").is_file() or (candidate / "数据/记录").is_dir():
            return candidate
    # A standalone skill can work in a new target directory without project docs.
    # In that case the caller must run from the intended project working directory.
    return current


def current_thread_id(explicit: str | None) -> str:
    thread_id = explicit or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    if not thread_id:
        raise SidecarError("缺少线程 ID；请在 Codex 线程中运行，或传入 --thread-id")
    return thread_id


def find_session_file(thread_id: str, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SidecarError(f"会话文件不存在：{path}")
        if thread_id not in path.name:
            raise SidecarError("会话文件名与线程 ID 不一致")
        return path

    sessions_root = Path.home() / ".codex" / "sessions"
    matches = list(sessions_root.glob(f"**/*{thread_id}*.jsonl"))
    if len(matches) != 1:
        raise SidecarError(f"按当前线程精确匹配到 {len(matches)} 个会话文件，拒绝猜测")
    return matches[0].resolve()


def default_state_dir(root: Path) -> Path:
    return root / ".goodidea" / "sidecar"


def state_path(state_dir: Path, thread_id: str) -> Path:
    return state_dir / "captures" / f"{thread_id}.json"


def lock_path(state_file: Path) -> Path:
    return state_file.with_suffix(".lock")


@contextmanager
def exclusive_lock(state_file: Path) -> Iterator[None]:
    lock = lock_path(state_file)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_state(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError as exc:
        raise SidecarError("当前线程没有活动的 Sidecar；请先运行 start") from exc
    if state.get("version") != VERSION:
        raise SidecarError("Sidecar 状态版本不受支持")
    return state


def read_complete_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        lines = handle.readlines()
    for index, raw in enumerate(lines, start=1):
        if not raw.endswith(b"\n"):
            # Codex may still be appending the final record. The watcher retries it.
            if index == len(lines):
                break
            raise SidecarError(f"会话文件第 {index} 行不完整")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SidecarError(f"会话文件第 {index} 行不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise SidecarError(f"会话文件第 {index} 行不是 JSON 对象")
        records.append(value)
    return records


def visible_event(record: dict[str, Any]) -> dict[str, Any] | None:
    if record.get("type") != "event_msg":
        return None
    payload = record.get("payload") or {}
    if payload.get("type") != "item_completed":
        return None
    item = payload.get("item") or {}
    item_type = item.get("type")
    if item_type not in {"UserMessage", "AgentMessage"}:
        return None

    content = item.get("content")
    if not isinstance(content, list):
        raise SidecarError("可见消息的 content 结构发生变化")
    pieces: list[str] = []
    for part in content:
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            raise SidecarError("可见消息的文本结构发生变化")
        pieces.append(part["text"])

    ordinal = record.get("ordinal")
    timestamp = record.get("timestamp")
    item_id = item.get("id")
    if not isinstance(ordinal, int) or not isinstance(timestamp, str) or not isinstance(item_id, str):
        raise SidecarError("可见消息缺少 ordinal、timestamp 或 id")

    return {
        "ordinal": ordinal,
        "timestamp": timestamp,
        "id": item_id,
        "speaker": USER_LABEL if item_type == "UserMessage" else AGENT_LABEL,
        "phase": item.get("phase") if item_type == "AgentMessage" else None,
        "text": "".join(pieces),
    }


def latest_ordinal(records: list[dict[str, Any]]) -> int:
    return max((value for record in records if isinstance((value := record.get("ordinal")), int)), default=-1)


def latest_turn_start(records: list[dict[str, Any]]) -> int:
    starts = [
        record["ordinal"]
        for record in records
        if record.get("type") == "event_msg"
        and (record.get("payload") or {}).get("type") == "task_started"
        and isinstance(record.get("ordinal"), int)
    ]
    if not starts:
        raise SidecarError("当前会话中找不到 task_started，无法确定当前轮次")
    return max(starts)


def latest_turn_id(records: list[dict[str, Any]]) -> str:
    starts = [
        (record["ordinal"], (record.get("payload") or {}).get("turn_id"))
        for record in records
        if record.get("type") == "event_msg"
        and (record.get("payload") or {}).get("type") == "task_started"
        and isinstance(record.get("ordinal"), int)
        and isinstance((record.get("payload") or {}).get("turn_id"), str)
    ]
    if not starts:
        raise SidecarError("当前会话中找不到 task_started，无法确定当前轮次")
    return max(starts)[1]


def turn_is_complete(records: list[dict[str, Any]], turn_id: str) -> bool:
    return any(
        record.get("type") == "event_msg"
        and (record.get("payload") or {}).get("type") == "task_complete"
        and (record.get("payload") or {}).get("turn_id") == turn_id
        for record in records
    )


def marker(thread_id: str, event: dict[str, Any]) -> str:
    return f"{MARKER_PREFIX}{thread_id}:{event['ordinal']}:{event['id']} -->"


def existing_markers(output: Path) -> set[str]:
    if not output.exists():
        return set()
    found: set[str] = set()
    with output.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped.startswith(MARKER_PREFIX) and stripped.endswith("-->"):
                found.add(stripped)
    return found


def local_minute(timestamp: str) -> str:
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SidecarError(f"无法解析消息时间：{timestamp}") from exc
    return instant.astimezone().strftime("%Y-%m-%d %H:%M")


def message_block(thread_id: str, event: dict[str, Any]) -> str:
    body = event["text"]
    if body and not body.endswith("\n"):
        body += "\n"
    return (
        f"\n{marker(thread_id, event)}\n"
        f"### {local_minute(event['timestamp'])}｜{event['speaker']}\n\n"
        f"{body}"
    )


def create_output(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "## 去向" in text:
            raise SidecarError("目标记录已有“去向”，视为已关闭，拒绝继续追加")
        if MESSAGES_END not in text:
            atomic_text_write(path, text.rstrip() + f"\n\n{MESSAGES_END}\n")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(
            "# 原始思考记录\n\n"
            "> 本记录由 Good Idea Sidecar 保存，只包含当前捕捉窗口中的完整可见对话；"
            "隐藏推理、工具输出、开发者指令和环境上下文不写入。\n\n"
            f"## 对话\n\n{MESSAGES_END}\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def sync_locked(state_file: Path, state: dict[str, Any]) -> tuple[dict[str, Any], int]:
    if state.get("status") not in {"active", "closing"}:
        return state, 0
    source = Path(state["session_file"])
    output = Path(state["output_file"])
    records = read_complete_json_lines(source)
    known = existing_markers(output)
    events = []
    for record in records:
        event = visible_event(record)
        if event and event["ordinal"] > state["start_ordinal"] and marker(state["thread_id"], event) not in known:
            events.append(event)
    events.sort(key=lambda event: event["ordinal"])

    if events:
        text = output.read_text(encoding="utf-8")
        if MESSAGES_END not in text:
            raise SidecarError("原始思考记录缺少消息边界标记，拒绝猜测插入位置")
        addition = "".join(message_block(state["thread_id"], event) for event in events)
        text = text.replace(MESSAGES_END, addition + f"\n{MESSAGES_END}", 1)
        atomic_text_write(output, text)

    state["last_ordinal"] = max(
        state.get("last_ordinal", state["start_ordinal"]),
        max((event["ordinal"] for event in events), default=state["start_ordinal"]),
    )
    state["source_size"] = source.stat().st_size
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    close_after = state.get("close_after_turn_id")
    if state.get("status") == "closing" and isinstance(close_after, str) and turn_is_complete(records, close_after):
        state["status"] = "closed"
        state["closed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_json_write(state_file, state)
    return state, len(events)


def sync_capture(state_file: Path) -> tuple[dict[str, Any], int]:
    with exclusive_lock(state_file):
        state = load_state(state_file)
        return sync_locked(state_file, state)


def spawn_watcher(script: Path, state_file: Path, log_file: Path, interval: float) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_file.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, str(script), "_watch", "--state-file", str(state_file), "--interval", str(interval)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()
    return process.pid


def command_start(args: argparse.Namespace) -> None:
    root = project_root()
    thread_id = current_thread_id(args.thread_id)
    source = find_session_file(thread_id, args.session_file)
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = (root / output).resolve()
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir(root)
    state_file = state_path(state_dir, thread_id)
    records = read_complete_json_lines(source)

    if args.from_point == "current-turn":
        start_ordinal = latest_turn_start(records)
    elif args.from_point == "now":
        start_ordinal = latest_ordinal(records)
    else:
        start_ordinal = -1

    with exclusive_lock(state_file):
        if state_file.exists():
            prior = load_state(state_file)
            if prior.get("status") == "active":
                raise SidecarError(f"当前线程已有活动捕捉：{prior['output_file']}")
        create_output(output)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        state = {
            "version": VERSION,
            "status": "active",
            "thread_id": thread_id,
            "session_file": str(source),
            "output_file": str(output),
            "start_ordinal": start_ordinal,
            "last_ordinal": start_ordinal,
            "source_size": 0,
            "watch_pid": None,
            "created_at": now,
            "updated_at": now,
        }
        atomic_json_write(state_file, state)
        state, count = sync_locked(state_file, state)

    if not args.no_watch:
        pid = spawn_watcher(Path(__file__).resolve(), state_file, state_dir / "logs" / f"{thread_id}.log", args.interval)
        with exclusive_lock(state_file):
            state = load_state(state_file)
            state["watch_pid"] = pid
            atomic_json_write(state_file, state)
    else:
        pid = None

    print(json.dumps({"status": "active", "captured": count, "watch_pid": pid, "output": str(output)}, ensure_ascii=False))


def command_sync(args: argparse.Namespace) -> None:
    root = project_root()
    thread_id = current_thread_id(args.thread_id)
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir(root)
    state, count = sync_capture(state_path(state_dir, thread_id))
    print(json.dumps({"status": state["status"], "captured": count, "output": state["output_file"]}, ensure_ascii=False))


def command_close(args: argparse.Namespace) -> None:
    root = project_root()
    thread_id = current_thread_id(args.thread_id)
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir(root)
    state_file = state_path(state_dir, thread_id)
    with exclusive_lock(state_file):
        state = load_state(state_file)
        if args.now:
            state["status"] = "closing"
            state.pop("close_after_turn_id", None)
            state, count = sync_locked(state_file, state)
            state["status"] = "closed"
            state["closed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            atomic_json_write(state_file, state)
        else:
            records = read_complete_json_lines(Path(state["session_file"]))
            state["status"] = "closing"
            state["close_after_turn_id"] = latest_turn_id(records)
            state, count = sync_locked(state_file, state)
    print(json.dumps({"status": state["status"], "captured": count, "output": state["output_file"]}, ensure_ascii=False))


def command_status(args: argparse.Namespace) -> None:
    root = project_root()
    thread_id = current_thread_id(args.thread_id)
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir(root)
    state = load_state(state_path(state_dir, thread_id))
    public = {key: state.get(key) for key in ("status", "thread_id", "output_file", "last_ordinal", "watch_pid", "updated_at")}
    print(json.dumps(public, ensure_ascii=False, indent=2))


def command_watch(args: argparse.Namespace) -> None:
    state_file = Path(args.state_file).resolve()
    stop = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    last_size = -1
    while not stop:
        try:
            state = load_state(state_file)
            if state.get("status") not in {"active", "closing"}:
                return
            source_size = Path(state["session_file"]).stat().st_size
            if source_size != last_size:
                state, _ = sync_capture(state_file)
                last_size = state.get("source_size", source_size)
        except Exception as exc:  # watcher must leave a durable, visible failure
            with state_file.with_suffix(".error.log").open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().astimezone().isoformat()} {exc}\n")
            return
        time.sleep(args.interval)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Good Idea Codex Sidecar")
    subparsers = result.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="注册当前线程并开始捕捉")
    start.add_argument("--output", required=True, help="原始思考 Markdown 路径")
    start.add_argument("--from", dest="from_point", choices=("current-turn", "now", "all"), default="current-turn")
    start.add_argument("--thread-id")
    start.add_argument("--session-file")
    start.add_argument("--state-dir")
    start.add_argument("--interval", type=float, default=0.5)
    start.add_argument("--no-watch", action="store_true", help="只注册并同步一次，不启动后台观察")
    start.set_defaults(function=command_start)

    for name, function in (("sync", command_sync), ("close", command_close), ("status", command_status)):
        child = subparsers.add_parser(name)
        child.add_argument("--thread-id")
        child.add_argument("--state-dir")
        if name == "close":
            child.add_argument("--now", action="store_true", help="立即关闭；默认等待当前轮最终回复写入")
        child.set_defaults(function=function)

    watch = subparsers.add_parser("_watch", help=argparse.SUPPRESS)
    watch.add_argument("--state-file", required=True)
    watch.add_argument("--interval", type=float, default=0.5)
    watch.set_defaults(function=command_watch)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.function(args)
    except SidecarError as exc:
        print(f"sidecar error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""按内容文件的 topics 字段统计母题成员。

只依赖标准库，避免为查询母题引入数据库或额外索引。
"""

from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIRS = {
    "闪念": ROOT / "数据/记录/闪念",
    "永久卡片": ROOT / "数据/记录/永久卡片",
    "外部来源": ROOT / "数据/记录/外部来源",
    "行动": ROOT / "数据/记录/行动",
    "有意思": ROOT / "数据/记录/有意思",
}
BEGIN = "<!-- topic-members:start -->"
END = "<!-- topic-members:end -->"


def frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end >= 0 else ""


def topics(text: str) -> set[str]:
    block = frontmatter(text)
    if not block:
        return set()
    match = re.search(r"(?m)^topics:[ \t]*(.*)$", block)
    if not match:
        return set()
    value = match.group(1).strip()
    found = set(re.findall(r"[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)[\"']?", value))
    if value in {"", "[]"}:
        found = set()
    # Also accept the common multiline form:
    start = match.end()
    for line in block[start:].splitlines():
        if not re.match(r"^\s+-\s+", line):
            if line.strip():
                break
            continue
        found.update(re.findall(r"-\s*[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)", line))
    return found


def status(text: str) -> str:
    block = frontmatter(text)
    match = re.search(r"(?m)^status:\s*[\"']?([^\"'\n]+)", block)
    return match.group(1).strip() if match else "未声明"


def members(topic_id: str) -> dict[str, list[tuple[Path, str, str]]]:
    result = {}
    for kind, directory in CONTENT_DIRS.items():
        matched = []
        # 闪念只查本层，避免把原始思考记录算成正式闪念；其他类型支持子目录。
        paths = directory.glob("*.md") if kind == "闪念" else directory.rglob("*.md")
        for path in sorted(paths):
            text = path.read_text(encoding="utf-8")
            if topic_id in topics(text):
                heading = re.search(r"(?m)^# +(.+)$", text)
                title = heading.group(1) if heading else path.stem
                matched.append((path, status(text), title))
        result[kind] = matched
    return result


def refresh(topic_id: str, groups: dict[str, list[tuple[Path, str, str]]]) -> None:
    candidates = []
    for path in (ROOT / "数据/记录/母题").glob("*.md"):
        block = frontmatter(path.read_text(encoding="utf-8"))
        if re.search(rf"(?m)^id:[ \t]*[\"']?{re.escape(topic_id)}[\"']?[ \t]*$", block):
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError(f"母题 ID {topic_id} 应对应一份文件，实际找到 {len(candidates)} 份")
    path = candidates[0]
    original = path.read_text(encoding="utf-8")
    if original.count(BEGIN) != 1 or original.count(END) != 1:
        raise ValueError("母题文件需要唯一的成员展示区域标记")
    before, rest = original.split(BEGIN)
    _, after = rest.split(END)
    lines = [BEGIN, "", "以下链接与数量由各内容的 `topics` 字段生成；正文仍保留在原文件中。", "",
             "| 内容类型 | 数量 |", "|---|---:|"]
    lines.extend(f"| {kind} | {len(items)} |" for kind, items in groups.items())
    lines.extend([f"| 合计 | {sum(map(len, groups.values()))} |", ""])
    for kind, items in groups.items():
        lines.extend([f"### {kind}（{len(items)} 份）", ""])
        for target, state, title in items:
            label = title.replace("[", "\\[").replace("]", "\\]")
            url = quote(os.path.relpath(target, path.parent), safe="/.-_")
            suffix = f" — {state}" if state != "未声明" else ""
            lines.append(f"- [{label}]({url}){suffix}")
        if not items:
            lines.append("暂无归属内容。")
        lines.append("")
    lines.append(END)
    rendered = before + "\n".join(lines) + after
    if rendered != original:
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
    print(f"已刷新母题成员展示：{path.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="统计一个母题的内容成员")
    parser.add_argument("topic_id", help="例如 good-idea")
    parser.add_argument("--refresh", action="store_true", help="将成员链接和数量刷新到对应母题文件")
    args = parser.parse_args()

    groups = members(args.topic_id)
    total = 0
    for kind, matched in groups.items():
        total += len(matched)
        print(f"{kind}: {len(matched)}")
        counts = Counter(s for _, s, _ in matched)
        if counts:
            print("  状态: " + "；".join(f"{name} {count}" for name, count in sorted(counts.items())))
        for path, state, _ in matched:
            print(f"  - {path.relative_to(ROOT)} [{state}]")
    print(f"总计: {total}")
    if args.refresh:
        refresh(args.topic_id, groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

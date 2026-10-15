#!/usr/bin/env python3
"""主动推送闪念的确定性素材层：扫描近期记录、挑候选卡、组装上下文、只读拉取外部数据、推送日志、发送时段。

只依赖标准库。所有输出为 JSON（UTF-8）。模型只做判断和写作，这里只做确定性的部分。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "数据/记录"
DIGEST_DIR = ROOT / "数据/摘要/近期关注"
LOG = ROOT / "数据/摘要/推送日志.jsonl"
EXT_DIR = ROOT / "数据/摘要/外部拉取"
SCAN_KINDS = {
    "闪念": (REC / "闪念", False),
    "原始思考记录": (REC / "闪念/原始思考记录", True),
    "行动": (REC / "行动", True),
    "外部来源": (REC / "外部来源", True),
    "永久卡片": (REC / "永久卡片", True),
    "有意思": (REC / "有意思", True),
}
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
LINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
WEREAD_URL = "https://i.weread.qq.com/api/agent/gateway"
WEREAD_SKILL_VERSION = "1.0.5"


def today() -> dt.date:
    return dt.date.today()


def out(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def rel(p: Path) -> str:
    return str(p.relative_to(REC))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end >= 0 else ""


def fm_status(text: str) -> str:
    m = re.search(r"(?m)^status:\s*[\"']?([^\"'\n]+)", frontmatter(text))
    return m.group(1).strip() if m else "未声明"


def fm_topics(text: str) -> list[str]:
    block = frontmatter(text)
    m = re.search(r"(?m)^topics:[ \t]*(.*)$", block)
    if not m:
        return []
    found = re.findall(r"[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)[\"']?", m.group(1))
    for line in block[m.end():].splitlines():
        if not re.match(r"^\s+-\s+", line):
            if line.strip():
                break
            continue
        found += re.findall(r"-\s*[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)", line)
    return sorted(set(found))


def title_of(text: str, p: Path) -> str:
    m = re.search(r"(?m)^# +(.+)$", text)
    return m.group(1).strip() if m else p.stem


def file_date(p: Path) -> dt.date | None:
    m = DATE_RE.match(p.name)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


def body(text: str) -> str:
    fm = frontmatter(text)
    return text[len(fm) + 7:] if fm else text


def links(text: str) -> list[str]:
    seen = []
    for l in LINK_RE.findall(text):
        l = l.strip()
        if l not in seen:
            seen.append(l)
    return seen


_ALL: list[Path] | None = None


def all_records() -> list[Path]:
    global _ALL
    if _ALL is None:
        _ALL = sorted(REC.rglob("*.md"))
    return _ALL


def resolve(link: str, base: Path | None = None) -> Path | None:
    """把 [[链接]] 解析为文件：先按相对 base、相对 数据/记录，再按文件名匹配。"""
    name = link if link.endswith(".md") else link + ".md"
    cands = []
    if base is not None:
        cands.append((base.parent / name))
    cands.append(REC / name)
    for c in cands:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c.is_file():
            return c
    stem = Path(name).name
    for p in all_records():
        if p.name == stem:
            return p
    return None


def resolve_card(arg: str) -> Path:
    for c in (Path(arg), REC / arg, ROOT / arg):
        if c.is_file():
            return c.resolve()
    r = resolve(arg)
    if r:
        return r
    sys.exit(f"找不到卡片：{arg}")


# ---------- scan ----------

def cmd_scan(a) -> None:
    start = today() - dt.timedelta(days=a.days)
    items = []
    for kind, (d, recursive) in SCAN_KINDS.items():
        if not d.is_dir():
            continue
        paths = d.rglob("*.md") if recursive else d.glob("*.md")
        for p in paths:
            fdate = file_date(p)
            mdate = dt.date.fromtimestamp(p.stat().st_mtime)
            hit = []
            if a.by in ("both", "name") and fdate and fdate >= start:
                hit.append("文件名日期")
            if a.by in ("both", "mtime") and mdate >= start:
                hit.append("修改时间")
            if not hit:
                continue
            t = read(p)
            items.append({
                "kind": kind, "date": fdate.isoformat() if fdate else None,
                "mtime": mdate.isoformat(), "matched_by": hit,
                "title": title_of(t, p), "status": fm_status(t),
                "topics": fm_topics(t), "path": rel(p),
            })
    items.sort(key=lambda x: (x["date"] or x["mtime"], x["path"]), reverse=True)
    mtime_only = [i for i in items if i["matched_by"] == ["修改时间"]]
    warn = None
    if len(mtime_only) >= 20:
        top = max({i["mtime"] for i in mtime_only}, key=lambda d: sum(i["mtime"] == d for i in mtime_only))
        n = sum(i["mtime"] == top for i in mtime_only)
        if n >= 0.8 * len(mtime_only):
            warn = f"{n} 条仅因修改时间命中且都在 {top}，疑似批量迁移/改写，可用 --by name 只看文件名日期"
    out({"by": a.by, "mtime_only": len(mtime_only), "warning": warn,"window": {"from": start.isoformat(), "to": today().isoformat(), "days": a.days},
         "count": len(items), "items": items})


# ---------- digest / log ----------

def latest_digest() -> Path | None:
    ds = sorted(DIGEST_DIR.glob("*.md")) if DIGEST_DIR.is_dir() else []
    return ds[-1] if ds else None


def sections(text: str) -> dict[str, str]:
    res, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            res[cur] = ""
        elif cur:
            res[cur] += line + "\n"
    return res


def read_log() -> list[dict]:
    if not LOG.is_file():
        return []
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def norm_card(s: str) -> str:
    return Path(s.removesuffix(".md")).name


def cjk_bigrams(s: str) -> set[str]:
    grams = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", s):
        grams.add(w.lower())
    return grams


STOP = set("一个 什么 这个 不是 就是 自己 我们 可以 没有 他们 还是 如何 为什么 而是 以及 一种 时候 已经 进行 如果 因为 所以".split())


# ---------- pool ----------

def cmd_pool(a) -> None:
    dg = latest_digest()
    focus_text, dropped, focus_links = "", set(), set()
    if dg:
        sec = sections(read(dg))
        for k, v in sec.items():
            if k.startswith("正在想的") or k.startswith("悬而未决"):
                focus_text += v
            if k.startswith("已落下"):
                dropped |= {norm_card(l) for l in links(v)}
        focus_links = {norm_card(l) for l in links(focus_text)}
    focus_grams = cjk_bigrams(focus_text) - STOP
    last_push: dict[str, str] = {}
    for r in read_log():
        k = norm_card(r.get("card", ""))
        if r.get("date") and r["date"] > last_push.get(k, ""):
            last_push[k] = r["date"]
    t0 = today()
    rows = []
    cards = sorted((REC / "闪念").glob("*.md"))
    # 关键词只算“稀有”二字词：在全部闪念里出现率 ≤ 15%，滤掉“产品”“价值的”这类泛词
    df: dict[str, int] = {}
    texts = {p: read(p) for p in cards}
    for p, t in texts.items():
        for g in cjk_bigrams(title_of(t, p)) | cjk_bigrams(body(t)[:1500]):
            df[g] = df.get(g, 0) + 1
    rare_max = max(2, int(len(cards) * 0.15))
    for p in cards:
        t = texts[p]
        if fm_status(t) != "待辨析":
            continue
        key = p.stem
        if key in dropped:
            continue
        created = file_date(p)
        out_links = links(t)
        shared_links = sorted({norm_card(l) for l in out_links} & focus_links)
        direct = key in focus_links
        grams = (cjk_bigrams(title_of(t, p)) | cjk_bigrams(body(t)[:1500])) - STOP
        shared_kw = sorted(g for g in grams & focus_grams if df.get(g, 0) <= rare_max)
        shelved = (t0 - created).days if created else 0
        lp = last_push.get(key)
        days_since_push = (t0 - dt.date.fromisoformat(lp)).days if lp else None
        s_direct = 6.0 if direct else 0.0
        s_links = 3.0 * len(shared_links)
        s_kw = round(min(len(shared_kw), 20) * 0.3, 2)
        s_shelf = min(shelved, 60) / 10
        penalty = 10.0 if days_since_push is not None and days_since_push < 7 else 0.0
        score = round(s_direct + s_links + s_kw + s_shelf - penalty, 2)
        rows.append({
            "path": rel(p), "title": title_of(t, p),
            "created": created.isoformat() if created else None,
            "days_shelved": shelved, "topics": fm_topics(t), "links": out_links,
            "last_push": lp, "score": score,
            "score_detail": {"在摘要中被引用": s_direct, "共享链接": s_links,
                             "共享关键词": s_kw, "搁置天数": s_shelf, "7天内已推扣分": -penalty},
            "shared_links": shared_links, "shared_keywords": shared_kw[:15],
        })
    rows.sort(key=lambda r: (-r["score"], r["path"]))
    out({"digest": str(dg.relative_to(ROOT)) if dg else None, "pool_size": len(rows),
         "excluded_dropped": sorted(dropped), "top": rows[: a.top]})


# ---------- context ----------

def excerpt(text: str, n: int) -> str:
    s = re.sub(r"\n{2,}", "\n", body(text)).strip()
    return s[:n] + ("…" if len(s) > n else "")


def cmd_context(a) -> None:
    card = resolve_card(a.card)
    text = read(card)
    budget = a.cap
    res = {"card": {"path": rel(card), "title": title_of(text, card), "status": fm_status(text),
                    "topics": fm_topics(text), "text": text[: int(budget * 0.6)]}}
    res["card"]["text_truncated"] = len(res["card"]["text"]) < len(text)
    budget -= len(res["card"]["text"])
    # 原始思考记录：被链接的，或同日期的
    raw_dir = REC / "闪念/原始思考记录"
    raws = []
    linked = [resolve(l, card) for l in links(text)]
    d = file_date(card)
    for p in sorted(raw_dir.glob("*.md")) if raw_dir.is_dir() else []:
        why = "被链接" if p in linked else ("同日期" if d and file_date(p) == d else None)
        if why and budget > 0:
            ex = excerpt(read(p), min(1500, budget))
            budget -= len(ex)
            raws.append({"path": rel(p), "reason": why, "excerpt": ex})
    res["raw_thoughts"] = raws
    outs, seen = [], {card} | {REC / r["path"] for r in raws}
    for l in links(text):
        p = resolve(l, card)
        if p is None:
            outs.append({"link": l, "missing": True})
            continue
        if p in seen or budget <= 0:
            continue
        seen.add(p)
        t = read(p)
        ex = excerpt(t, min(300, budget))
        budget -= len(ex)
        outs.append({"path": rel(p), "title": title_of(t, p), "excerpt": ex})
    backs = []
    for p in all_records():
        if p in seen:
            continue
        t = read(p)
        if any(resolve(l, p) == card for l in links(t) if norm_card(l) == card.stem):
            seen.add(p)
            if budget <= 0:
                backs.append({"path": rel(p), "title": title_of(t, p), "excerpt": None})
                continue
            ex = excerpt(t, min(300, budget))
            budget -= len(ex)
            backs.append({"path": rel(p), "title": title_of(t, p), "excerpt": ex})
    res["outlinks"], res["backlinks"] = outs, backs
    res["chars_used"] = a.cap - max(budget, 0)
    res["cap"] = a.cap
    res["truncated"] = budget <= 0
    out(res)


# ---------- external ----------

def weread(api: str, **params):
    key = os.environ.get("WEREAD_API_KEY")
    if not key:
        raise RuntimeError("未设置 WEREAD_API_KEY")
    data = json.dumps({"api_name": api, **params, "skill_version": WEREAD_SKILL_VERSION}).encode()
    req = urllib.request.Request(WEREAD_URL, data=data, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if resp.get("errcode", 0) not in (0, None):
        raise RuntimeError(f"{api} errcode={resp.get('errcode')} {resp.get('errmsg', '')}")
    if resp.get("upgrade_info"):
        print(f"[weread] upgrade_info: {resp['upgrade_info']}", file=sys.stderr)
    return resp


def ts2d(ts) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else None
    except (TypeError, ValueError):
        return None


def pull_weread(days: int, errors: list) -> dict:
    since = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    res = {"recent_books": [], "recent_notes": [], "latest_note_books": []}
    try:
        shelf = weread("/shelf/sync")
        for b in shelf.get("books", []):
            if (b.get("readUpdateTime") or 0) >= since:
                res["recent_books"].append({
                    "bookId": b.get("bookId"), "title": b.get("title"), "author": b.get("author"),
                    "readUpdate": ts2d(b.get("readUpdateTime")), "finished": b.get("finishReading")})
        res["recent_books"].sort(key=lambda x: x["readUpdate"] or "", reverse=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"weread /shelf/sync: {e}")
    try:
        nb = weread("/user/notebooks", count=20)
        books = nb.get("books", [])
        for b in books[:5]:
            res["latest_note_books"].append({
                "bookId": b.get("bookId"), "title": (b.get("book") or {}).get("title"),
                "author": (b.get("book") or {}).get("author"), "lastNote": ts2d(b.get("sort")),
                "progress": b.get("readingProgress"),
                "counts": {"划线": b.get("noteCount"), "想法": b.get("reviewCount")}})
        targets = [b for b in books if (b.get("sort") or 0) >= since][:5]
        for b in targets:
            bid, title = b.get("bookId"), (b.get("book") or {}).get("title")
            res["recent_notes"] += book_notes(bid, title, since, errors)
        res["recent_notes"].sort(key=lambda x: x["date"] or "", reverse=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"weread /user/notebooks: {e}")
    # 窗口内在读的书：不论时间，带上他在这本书里的全部旧笔记（满分样稿的“五个月前那句”就来自这里）
    res["recent_book_notes"] = []
    for b in res["recent_books"][:5]:
        ns = book_notes(b["bookId"], b["title"], 0, errors)
        if ns:
            res["recent_book_notes"].append({"book": b["title"], "author": b["author"],
                                             "notes": sorted(ns, key=lambda x: x["date"] or "")[-20:]})
    return res


def book_notes(bid, title, since, errors: list) -> list:
    """单本书的划线与想法（createTime >= since）。只读接口。"""
    notes = []
    try:
        bm = weread("/book/bookmarklist", bookId=bid)
        for u in bm.get("updated", []):
            if (u.get("createTime") or 0) >= since:
                notes.append({"type": "划线", "book": title, "date": ts2d(u.get("createTime")),
                              "text": u.get("markText")})
    except Exception as e:  # noqa: BLE001
        errors.append(f"weread bookmarklist {title}: {e}")
    try:
        rv = weread("/review/list/mine", bookid=bid, count=50)
        for r in rv.get("reviews", []):
            r = r.get("review", r)
            if (r.get("createTime") or 0) >= since:
                notes.append({"type": "想法", "book": title, "date": ts2d(r.get("createTime")),
                              "abstract": r.get("abstract"), "text": r.get("content")})
    except Exception as e:  # noqa: BLE001
        errors.append(f"weread review/list/mine {title}: {e}")
    return notes


def pull_getnote(days: int, limit: int, errors: list) -> list:
    since = (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    cmd = ["getnote", "notes", "-o", "json", "--limit", str(limit), "--created-from", since]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout).strip()[:300])
        data = json.loads(p.stdout)
        if not data.get("success", True):
            raise RuntimeError(str(data.get("error"))[:300])
        notes = (data.get("data") or {}).get("notes", [])
        if (data.get("data") or {}).get("has_more"):
            errors.append(f"getnote: 窗口内超过 {limit} 条，只取了前 {limit} 条（可加 --limit）")
        return [{"id": n.get("note_id") or n.get("id"), "created": n.get("created_at"),
                 "title": n.get("display_title") or n.get("title"), "type": n.get("note_type"),
                 "tags": [t.get("name") for t in n.get("tags") or []],
                 "url": n.get("note_url"),
                 "content": (n.get("content") or "")[:800]} for n in notes]
    except FileNotFoundError:
        errors.append("getnote: 未找到 getnote CLI")
    except Exception as e:  # noqa: BLE001
        errors.append(f"getnote: {e}")
    return []


def cmd_external(a) -> None:
    errors: list[str] = []
    res = {"date": today().isoformat(), "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
           "days": a.days, "weread": pull_weread(a.days, errors),
           "getnote": pull_getnote(a.days, a.limit, errors), "errors": errors}
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    f = EXT_DIR / f"{today().isoformat()}.json"
    f.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    w = res["weread"]
    out({"saved": str(f.relative_to(ROOT)), "errors": errors,
         "summary": {"weread_recent_books": len(w["recent_books"]), "weread_recent_notes": len(w["recent_notes"]),
                     "getnote_notes": len(res["getnote"])}})


# ---------- log ----------

def cmd_log(a) -> None:
    card = resolve_card(a.card)
    now = dt.datetime.now()
    row = {"date": now.date().isoformat(), "time": now.strftime("%H:%M"), "card": rel(card), "reaction": a.reaction}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out({"appended": row, "log": str(LOG.relative_to(ROOT))})


# ---------- slot ----------

ANCHOR_WORK_SAT = dt.date(2026, 10, 17)  # 大小周：这个周六上班，此后隔周交替
WORK_SLOTS = [("午间", "12:00", "13:00"), ("下班路上", "17:40", "18:30"), ("晚上", "20:00", "21:30")]
REST_SLOTS = [("上午晚些", "10:00", "11:30"), ("下午", "15:00", "16:30"), ("晚上", "20:00", "21:30")]


def is_workday(d: dt.date) -> tuple[bool, str]:
    wd = d.weekday()
    if wd < 5:
        return True, "工作日"
    if wd == 6:
        return False, "周日休息"
    weeks = (d - ANCHOR_WORK_SAT).days // 7
    return (True, "大小周：本周六上班") if weeks % 2 == 0 else (False, "大小周：本周六休息")


def hm(s: str) -> dt.time:
    return dt.time.fromisoformat(s)


def cmd_slot(a) -> None:
    d = dt.date.fromisoformat(a.date) if a.date else today()
    work, why = is_workday(d)
    slots = WORK_SLOTS if work else REST_SLOTS
    seed = int(hashlib.sha256(f"goodidea-push-{d.isoformat()}".encode()).hexdigest(), 16)
    rng = random.Random(seed)
    name, s, e = rng.choice(slots)
    st, en = hm(s), hm(e)
    span = (en.hour * 60 + en.minute) - (st.hour * 60 + st.minute)
    m = st.hour * 60 + st.minute + rng.randrange(span)
    now = dt.datetime.now()
    inside = (now.date() == d) and st <= now.time().replace(second=0, microsecond=0) <= en
    out({"date": d.isoformat(), "weekday": "一二三四五六日"[d.weekday()], "workday": work, "reason": why,
         "slot": name, "window": [s, e], "suggested_time": f"{m // 60:02d}:{m % 60:02d}",
         "now": now.strftime("%Y-%m-%d %H:%M") if now.date() == d else None, "inside_window": inside,
         "candidates": [x[0] for x in slots]})


def main() -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="列出窗口内的近期记录"); p.add_argument("--days", type=int, default=14)
    p.add_argument("--by", choices=["both", "name", "mtime"], default="both", help="按文件名日期/修改时间/两者命中")
    p.set_defaults(f=cmd_scan)
    p = sub.add_parser("pool", help="待辨析闪念候选池打分"); p.add_argument("--top", type=int, default=3)
    p.set_defaults(f=cmd_pool)
    p = sub.add_parser("context", help="组装一张卡的上下文"); p.add_argument("card")
    p.add_argument("--cap", type=int, default=6000, help="总字符上限"); p.set_defaults(f=cmd_context)
    p = sub.add_parser("external", help="只读拉取微信读书与得到大脑")
    p.add_argument("--days", type=int, default=14); p.add_argument("--limit", type=int, default=50)
    p.set_defaults(f=cmd_external)
    p = sub.add_parser("log", help="追加推送日志"); p.add_argument("card")
    p.add_argument("--reaction", required=True); p.set_defaults(f=cmd_log)
    p = sub.add_parser("slot", help="今天的发送时段"); p.add_argument("--date", help="YYYY-MM-DD，默认今天")
    p.set_defaults(f=cmd_slot)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()

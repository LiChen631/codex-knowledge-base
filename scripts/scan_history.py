#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_history.py -- mine past agent sessions into the knowledge base.

Sources
-------
1. codex_thread_db : ~/.codex/state_5.sqlite (threads) +
                     ~/.codex/thread_history_1.sqlite (thread_items)
                     -> authoritative, structured, covers every saved thread.
2. codex_sessions  : ~/.codex/sessions/**/rollout-*.jsonl[.zst]
                     -> raw archive; parsed only for threads absent from the DB.
3. dsh_sessions    : D:\\dsh-home\\sessions/**/session.jsonl.zstd  (DeepSeek Harness)
4. chatgpt_export  : any local export found; otherwise recorded as missing.

Everything is appended idempotently: re-running replaces the rows of a source.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402

HOME = Path(os.path.expanduser("~"))
CODEX = HOME / ".codex"
STAGING = kb.LOGS / "import"

GH_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")
ERR_HINTS = re.compile(
    r"(?i)(traceback \(most recent call last\)|^error[:\s]|error:|exception|not recognized as"
    r"|commandnotfound|enoent|cannot find|failed to|no such file|permission denied"
    r"|\bitem has already been added\b|0x[0-9a-f]{8})")
PWSH_RE = re.compile(r'^"?[^"]*?(pwsh|powershell)(\.exe)?"?\s+(-NoProfile\s+)?-Command\s+', re.I)


def _threads_meta() -> dict:
    p = CODEX / "state_5.sqlite"
    if not p.exists():
        return {}
    con = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    out = {}
    try:
        for r in con.execute(
                "SELECT id,title,cwd,created_at,updated_at,first_user_message,archived,"
                " git_origin_url,cli_version,model,originator FROM threads"):
            out[r["id"]] = dict(r)
    finally:
        con.close()
    return out


RISK_RE = re.compile(
    r"(?i)(\brm\s+-rf\b|\bdel\s+/f\b|\bRemove-Item\b[^\n]*-Recurse\b|\bgit\s+reset\s+--hard\b"
    r"|\bgit\s+clean\s+-[a-z]*f|\breg\s+delete\b|\bformat\s+[a-z]:|\bdiskpart\b|--no-verify"
    r"|\bshutdown\b|\bRestart-Computer\b|\bSet-ExecutionPolicy\b)")

SKIP_PREFIX = re.compile(
    r"^\s*(?:cd\s+[^;|&]*|#[^\n]*|\$env:[A-Za-z0-9_]+\s*=.*|\$[A-Za-z_][A-Za-z0-9_]*\s*=.*|set\s+\S+=.*"
    r"|echo\s+.*|Write-Output\b.*|Write-Host\b.*|chcp\s+\d+|cls\b|title\s+.*"
    r"|\|.*|&\s*$)", re.I)

STRONG_ERR = re.compile(
    r"(?i)(traceback \(most recent call last\)|^error[\s:]|\berror ts\d+|\bERR_[A-Z0-9_]+"
    r"|not recognized as an internal or external command|command not found|no such file or directory"
    r"|permission denied|^fatal:|cannot find module|ModuleNotFoundError|^ImportError|"
    r"^SyntaxError|^ParserError|^AssertionError|npm ERR!|^pip.*error|^FAILED\b|^E\s+\w+Error|"
    r"\bException\b|docker: |dial tcp|connection refused|401 Unauthorized|403 Forbidden|404 Not Found)")


def norm_cmd(cmd: str) -> str:
    c = (cmd or "").strip()
    c = PWSH_RE.sub("", c)
    c = c.strip().strip("'\"")
    # repair quoting artifacts seen in recorded PowerShell wrappers: "'$x" -> "$x"
    c = c.replace("'$", "$").replace("'\"", '"')
    c = re.sub(r"[ \t]+", " ", c)
    return c[:600]


def _effective(cmd: str) -> str:
    """Return the first meaningful command, skipping cd/comment/env-assignment noise."""
    c = norm_cmd(cmd)
    if "@'" in c or '@"' in c:
        m = re.search(r"\|\s*(Set-Content|Add-Content|Out-File|Tee-Object|python3?|bash|sh)\b", c, re.I)
        if m:
            return m.group(1) + " (heredoc)"
        return ""
    parts = [x.strip() for x in re.split(r"[\r\n;]+|&&", c) if x.strip()]
    for part in parts:
        part = re.sub(r"^&\s+", "", part)
        part = re.sub(r"^\"[^\"]*\.exe\"\s+", "", part, flags=re.I)
        part = re.sub(r"^'[^']*\.exe'\s+", "", part, flags=re.I)
        part = re.sub(r"^[A-Za-z]:\\[^\s']*\.exe\s+", "", part, flags=re.I)
        part = part.lstrip("\"'")
        if part.lower().startswith("env "):
            part = part[4:]
        if SKIP_PREFIX.match(part):
            continue
        return part
    return parts[0] if parts else ""


def cmd_signature(cmd: str) -> tuple[str, str, str]:
    """(signature, tool, verb) built from the first effective command in a chain."""
    eff = _effective(cmd)
    if not eff:
        return ("", "", "")
    toks = [t for t in re.split(r"[\s|;&]+", eff) if t]
    raw = toks[0].strip("'\"")
    tool = os.path.basename(raw).lower()
    tool = re.sub(r"\.(exe|cmd|bat|ps1)$", "", tool)
    tool = re.sub(r"[^a-z0-9_\-]", "", tool) or raw[:20].lower()
    verb = ""
    rest = toks[1:6]
    if tool in ("bash", "sh", "zsh", "powershell", "pwsh", "cmd", "wsl", "wsl.exe"):
        return (tool, tool, "")
    if tool in ("python", "python3", "py") and rest and rest[0] == "-m":
        rest = rest[1:]
    for t in rest:
        tt = t.lower().strip("'\"")
        if tt.startswith("-"):
            continue
        if re.fullmatch(r"[a-z][a-z0-9_\-]{1,20}", tt):
            verb = tt
            break
    return (("%s %s" % (tool, verb)) if verb else tool, tool, verb)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CODE_LINE_RE = re.compile(
    r"^\s*(except\b|raise\b|if\b|for\b|while\b|return\b|def\b|class\b|elif\b|else\s*:|"
    r"try\s*:|with\b|import\b|from\b|print\(|#|//|\$|const\b|let\b|function\b"
    r"|[A-Za-z_][\w\.]*\s*=\s|-?\s*\|\s)")
GENERIC_RE = re.compile(r"^\s*(Failed|Error|ERROR|Warning|WARN)[:：]?\s*[0-9]*\s*$")


def clean(text: str) -> str:
    return ANSI_RE.sub("", text or "")


NOISE_RE = re.compile(
    r"(?i)(\[troubleshooting\]|\[workflow\]|\[skill\]|\[software\]|\[script\]|"
    r"\[mcp\]|\[github_project\]|score=|复用前请核对|命中知识库|知识库已加载|"
    r"Exception class for|__doc__|docstring)")
NUMERIC_NOISE_RE = re.compile(r"^\s*-?\s*\d+\s+\w+\s+(None|\d+|True|False)\b")
QUOTE_NOISE_RE = re.compile('"' * 3)


def _is_error_line(line: str) -> bool:
    l = line.strip()
    if len(l) < 12 or len(l) > 400:
        return False
    if CODE_LINE_RE.match(l) or GENERIC_RE.match(l):
        return False
    if NOISE_RE.search(l) or NUMERIC_NOISE_RE.match(l):
        return False
    if QUOTE_NOISE_RE.search(l):
        return False
    return bool(STRONG_ERR.search(l))


def looks_like_error(text: str) -> bool:
    if not text:
        return False
    lines = [l.strip() for l in clean(text).splitlines() if l.strip()]
    sample = lines[:6] + lines[-25:]
    return any(_is_error_line(l) for l in sample)


def upsert_command(con, cmd: str, ok: bool, cwd: str, ts: str):
    sig, tool, verb = cmd_signature(cmd)
    if not sig:
        return
    risk = "high" if RISK_RE.search(cmd) else "normal"
    row = con.execute("SELECT id,uses,ok_count,fail_count,cwds FROM commands WHERE signature=?", (sig,)).fetchone()
    if row:
        cwds = set(filter(None, (row["cwds"] or "").split(";")))
        if cwd:
            cwds.add(cwd)
        con.execute(
            "UPDATE commands SET uses=uses+1, ok_count=ok_count+?, fail_count=fail_count+?,"
            " cwds=?, last_seen=CASE WHEN ? > last_seen THEN ? ELSE last_seen END,"
            " sample=CASE WHEN length(sample) < 12 THEN ? ELSE sample END WHERE id=?",
            (1 if ok else 0, 0 if ok else 1, ";".join(sorted(cwds))[:400],
             ts, ts, norm_cmd(cmd)[:600], row["id"]))
    else:
        con.execute(
            "INSERT INTO commands(signature,tool,verb,sample,uses,ok_count,fail_count,cwds,"
            " risk,first_seen,last_seen) VALUES(?,?,?,?,1,?,?,?,?,?,?)",
            (sig, tool, verb, norm_cmd(cmd)[:600], 1 if ok else 0, 0 if ok else 1,
             (cwd or "")[:400], risk, ts, ts))


def upsert_error(con, text: str, ts: str, cmd: str = ""):
    lines = [l.strip() for l in clean(text).splitlines() if l.strip()]
    key = ""
    for l in lines:
        if _is_error_line(l):
            key = l
            break
    key = re.sub(r"\s+", " ", key)[:220]
    if not key:
        return
    row = con.execute("SELECT id FROM errors WHERE signature=?", (key,)).fetchone()
    if row:
        con.execute("UPDATE errors SET occurrences=occurrences+1,"
                    " last_seen=CASE WHEN ? > last_seen THEN ? ELSE last_seen END WHERE id=?",
                    (ts, ts, row["id"]))
    else:
        con.execute(
            "INSERT INTO errors(signature,error_text,occurrences,first_seen,last_seen,"
            "fix_commands) VALUES(?,?,1,?,?,?)",
            (key, text[:1500], ts, ts, norm_cmd(cmd)[:400]))


def _reset_source(con, source_id: str):
    con.execute("DELETE FROM history_items WHERE source_id=?", (source_id,))


def scan_codex_db(con, meta) -> tuple[int, int]:
    p = CODEX / "thread_history_1.sqlite"
    if not p.exists():
        return (0, 0)
    _reset_source(con, "codex_thread_db")
    src = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True)
    src.row_factory = sqlite3.Row
    n_items = n_threads = 0
    seen_threads = set()
    try:
        rows = src.execute(
            "SELECT thread_id,turn_id,created_at_ms,item_type,item_json FROM thread_items"
            " ORDER BY thread_id, rollout_ordinal")
        for r in rows:
            tid = r["thread_id"]
            seen_threads.add(tid)
            m = meta.get(tid, {})
            title = (m.get("title") or "")[:120]
            cwd = m.get("cwd") or ""
            ts = ""
            try:
                import datetime as dt
                ts = dt.datetime.fromtimestamp((r["created_at_ms"] or 0) / 1000).astimezone().isoformat(timespec="seconds")
            except Exception:
                pass
            try:
                o = json.loads(r["item_json"])
            except Exception:
                continue
            t = o.get("type") or r["item_type"]
            kind = name = detail = status = None
            if t == "commandExecution":
                cmd = o.get("command") or ""
                kind, status = "command", o.get("status") or ""
                name = cmd_signature(cmd)[0]
                detail = norm_cmd(cmd)
                out_txt = clean(o.get("aggregatedOutput") or "")
                if status != "completed" or looks_like_error(out_txt):
                    upsert_error(con, out_txt, ts, cmd)
            elif t == "mcpToolCall":
                kind, status = "mcp", o.get("status") or ""
                name = "%s/%s" % (o.get("server") or "?", o.get("tool") or "?")
                detail = json.dumps(o.get("arguments") or {}, ensure_ascii=False)[:800]
            elif t == "fileChange":
                kind, status = "file", o.get("status") or ""
                paths = [c.get("path") for c in (o.get("changes") or [])]
                name = os.path.basename(paths[0]) if paths else ""
                detail = "; ".join(p or "" for p in paths)[:800]
            elif t == "webSearch":
                kind = "websearch"
                name = (o.get("query") or "")[:160]
                detail = name
            elif t in ("userMessage", "agentMessage"):
                kind = "message"
                name = t
                texts = []
                for c in (o.get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(c.get("text") or "")
                detail = "\n".join(texts)[:600]
            elif t == "collabAgentToolCall":
                kind, status = "agent", o.get("status") or ""
                name = o.get("tool") or "collab"
                detail = json.dumps(o.get("arguments") or {}, ensure_ascii=False)[:400]
            elif t in ("imageView",):
                kind, name = "image", "view_image"
                detail = str(o.get("path") or "")[:300]
            else:
                continue
            con.execute(
                "INSERT INTO history_items(source_id,thread_id,thread_title,cwd,ts,kind,name,detail,status,extra)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("codex_thread_db", tid, title, cwd, ts, kind, (name or "")[:200], detail, status, None))
            n_items += 1
            if kind == "command":
                upsert_command(con, detail, status == "completed", cwd, ts)
        n_threads = len(seen_threads)
    finally:
        src.close()
    con.execute(
        "INSERT OR REPLACE INTO history_sources(id,label,kind,location,status,items,threads,bytes,scanned_at,detail)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("codex_thread_db", "Codex 会话数据库", "codex",
         str(CODEX / "thread_history_1.sqlite"), "scanned", n_items, n_threads,
         (CODEX / "thread_history_1.sqlite").stat().st_size, kb.now(),
         "来自 thread_history_1.sqlite（结构化 item），覆盖本地全部线程"))
    return (n_items, n_threads)


def _zstd_decompress(src: Path, dst: Path) -> bool:
    """Decompress with CPython 3.14 (compression.zstd) or `zstd` if present."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    helper = shutil.which("zstd")
    if helper:
        r = subprocess.run([helper, "-d", "-f", "-q", str(src), "-o", str(dst)],
                           capture_output=True)
        return r.returncode == 0 and dst.exists()
    py314 = Path(r"D:\Python314\python.exe")
    if not py314.exists():
        py314 = Path(sys.executable)
    code = (
        "import compression.zstd,sys,pathlib;"
        "src,dst=sys.argv[1],sys.argv[2];"
        "pathlib.Path(dst).write_bytes(compression.zstd.decompress(pathlib.Path(src).read_bytes()))"
    )
    r = subprocess.run([str(py314), "-c", code, str(src), str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        print("  [zstd] failed:", src.name, (r.stderr or "")[:200])
    return r.returncode == 0 and dst.exists()


def scan_rollouts(con, meta, known_threads: set) -> tuple[int, int, int]:
    """Record rollout inventory; parse only rollouts whose thread is not in the DB."""
    root = CODEX / "sessions"
    if not root.exists():
        return (0, 0, 0)
    files = sorted([p for p in root.rglob("rollout-*.jsonl*") if p.is_file()],
                   key=lambda p: p.stat().st_mtime)
    total_bytes = sum(p.stat().st_size for p in files)
    parsed_items = parsed_threads = 0
    _reset_source(con, "codex_sessions")
    for p in files:
        m = re.search(r"rollout-[0-9T\-]+-([0-9a-f\-]{36})", p.name)
        tid = m.group(1) if m else p.stem
        if tid in known_threads:
            continue
        target = p
        if p.suffix == ".zst":
            target = STAGING / "rollouts" / p.name.replace(".zst", "")
            if not target.exists() and not _zstd_decompress(p, target):
                continue
        try:
            with open(target, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    pl = o.get("payload") or {}
                    if pl.get("type") not in ("function_call", "custom_tool_call"):
                        continue
                    name = pl.get("name") or ""
                    args = pl.get("arguments") or ""
                    if name in ("shell", "exec_command", "local_shell"):
                        try:
                            a = json.loads(args)
                            cmd = a.get("command")
                            if isinstance(cmd, list):
                                cmd = " ".join(cmd)
                        except Exception:
                            cmd = args
                        con.execute(
                            "INSERT INTO history_items(source_id,thread_id,thread_title,cwd,ts,kind,name,detail,status)"
                            " VALUES(?,?,?,?,?,?,?,?,?)",
                            ("codex_sessions", tid, (meta.get(tid, {}) or {}).get("title", ""),
                             (meta.get(tid, {}) or {}).get("cwd", ""), o.get("timestamp", ""),
                             "command", cmd_signature(cmd or "")[0], norm_cmd(cmd or ""), "completed"))
                        upsert_command(con, cmd or "", True, (meta.get(tid, {}) or {}).get("cwd", ""),
                                       o.get("timestamp", ""))
                        parsed_items += 1
            parsed_threads += 1
        except Exception as e:
            print("  [rollout] skip", p.name, e)
    con.execute(
        "INSERT OR REPLACE INTO history_sources(id,label,kind,location,status,items,threads,bytes,scanned_at,detail)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("codex_sessions", "Codex rollout 原始存档", "codex", str(root), "scanned",
         parsed_items, parsed_threads, total_bytes, kb.now(),
         "共 %d 个 rollout 文件；%d 个已由 thread_history 覆盖，额外解析 %d 个" % (
             len(files), len(files) - parsed_threads, parsed_threads)))
    return (len(files), parsed_items, parsed_threads)


def scan_dsh(con) -> tuple[int, int, int]:
    root = Path(r"D:\dsh-home\sessions")
    if not root.exists():
        return (0, 0, 0)
    files = sorted(root.rglob("session.jsonl.zstd"))
    _reset_source(con, "dsh_sessions")
    n_items = n_threads = 0
    total = sum(p.stat().st_size for p in files)
    for p in files:
        sid = p.parent.name
        out = STAGING / "dsh" / (sid + ".jsonl")
        if not out.exists() and not _zstd_decompress(p, out):
            continue
        n_threads += 1
        title = ""
        ts = ""
        try:
            for line in open(out, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                t = o.get("type")
                if t == "session/title":
                    title = str((o.get("data") or {}).get("title") or "")[:120]
                if t == "tool/call":
                    d = o.get("data") or {}
                    name = d.get("name") or ""
                    args = d.get("arguments") or ""
                    cmd = ""
                    try:
                        a = json.loads(args)
                        cmd = a.get("command") or ""
                    except Exception:
                        cmd = ""
                    con.execute(
                        "INSERT INTO history_items(source_id,thread_id,thread_title,cwd,ts,kind,name,detail,status)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        ("dsh_sessions", sid, title, r"D:\deepseekharness",
                         str(o.get("time") or ""), "command", name,
                         norm_cmd(cmd) if cmd else str(args)[:400], "completed"))
                    if cmd:
                        upsert_command(con, cmd, True, r"D:\deepseekharness", str(o.get("time") or ""))
                    n_items += 1
                elif t == "tool/result":
                    d = o.get("data") or {}
                    txt = json.dumps(d, ensure_ascii=False)
                    if looks_like_error(txt):
                        upsert_error(con, txt[:1200], str(o.get("time") or ""))
                elif t == "user/message":
                    txt = json.dumps(o.get("data") or {}, ensure_ascii=False)[:400]
                    con.execute(
                        "INSERT INTO history_items(source_id,thread_id,thread_title,cwd,ts,kind,name,detail,status)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        ("dsh_sessions", sid, title, r"D:\deepseekharness",
                         str(o.get("time") or ""), "message", "user", txt, ""))
                    n_items += 1
        except Exception as e:
            print("  [dsh] skip", sid, e)
    con.execute(
        "INSERT OR REPLACE INTO history_sources(id,label,kind,location,status,items,threads,bytes,scanned_at,detail)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("dsh_sessions", "DeepSeek Harness 会话", "dsh", str(root), "scanned",
         n_items, n_threads, total, kb.now(),
         "zstd 压缩的 session.jsonl；解压缓存于 logs/import/dsh"))
    return (len(files), n_items, n_threads)


def scan_chatgpt(con) -> str:
    """Look for locally available ChatGPT exports; never claim more than was found."""
    candidates = [
        HOME / "Downloads", HOME / "Desktop", Path("D:/BaiduNetdiskDownload"),
        Path("D:/Download"), Path("D:/Documents_backup"),
    ]
    hits = []
    for base in candidates:
        if not base.exists():
            continue
        try:
            for p in base.rglob("*conversations*.json"):
                hits.append(p)
            for p in base.rglob("chatgpt*export*.zip"):
                hits.append(p)
        except Exception:
            continue
    if hits:
        con.execute(
            "INSERT OR REPLACE INTO history_sources(id,label,kind,location,status,items,threads,bytes,scanned_at,detail)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("chatgpt_export", "ChatGPT 历史导出（本地文件）", "chatgpt",
             "; ".join(str(h) for h in hits[:5]), "partial", 0, 0,
             sum(h.stat().st_size for h in hits), kb.now(),
             "发现 %d 个疑似导出文件，需人工确认后导入" % len(hits)))
        return "partial"
    con.execute(
        "INSERT OR REPLACE INTO history_sources(id,label,kind,location,status,items,threads,bytes,scanned_at,detail)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("chatgpt_export", "ChatGPT 历史导出（本地文件）", "chatgpt",
         "N/A", "inaccessible", 0, 0, 0, kb.now(),
         "本地未发现导出文件；ChatGPT 网页历史无公开 API 可读，需用户导出 conversations.json"))
    return "inaccessible"


def main():
    kb.migrate()
    con = kb.connect()
    t0 = kb.now()
    try:
        meta = _threads_meta()
        print("[scan] Codex 线程元数据:", len(meta))
        n_items, n_threads = scan_codex_db(con, meta)
        print("[scan] Codex 结构化记录: %d 条 / %d 线程" % (n_items, n_threads))
        known = {r[0] for r in con.execute(
            "SELECT DISTINCT thread_id FROM history_items WHERE source_id='codex_thread_db'")}
        files, extra_items, extra_threads = scan_rollouts(con, meta, known)
        print("[scan] rollout 文件 %d 个（额外解析 %d）" % (files, extra_threads))
        dfiles, ditems, dthreads = scan_dsh(con)
        print("[scan] DSH 会话 %d 个 / %d 条工具调用" % (dthreads, ditems))
        status = scan_chatgpt(con)
        print("[scan] ChatGPT 本地导出:", status)
        con.execute("DELETE FROM history_fts")
        for r in con.execute("SELECT id,name,detail,thread_title FROM history_items"):
            con.execute("INSERT INTO history_fts(id,name,detail,thread_title) VALUES(?,?,?,?)",
                        (r["id"], r["name"] or "", r["detail"] or "", r["thread_title"] or ""))
        kb.kv_set(con, "last_history_scan", t0)
        con.commit()
        s = con.execute("SELECT COUNT(*) FROM history_items").fetchone()[0]
        c = con.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
        e = con.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
        print("[scan] 汇总: history_items=%d commands=%d errors=%d" % (s, c, e))
    finally:
        con.close()


if __name__ == "__main__":
    main()

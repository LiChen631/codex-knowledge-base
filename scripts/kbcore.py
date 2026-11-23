#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kbcore.py -- Codex Global Knowledge Base core library.

Zero third-party dependencies. Python 3.10+ / SQLite 3.34+ (FTS5 trigram).

Design notes
------------
* Every mutation goes through ``upsert_asset`` so that the FTS mirror stays in sync.
* All timestamps are ISO-8601 with local offset, produced by ``now()``.
* The module never stores secret values; ``scan_secrets`` is used before any
  export/commit to keep tokens out of Markdown/SQLite/Git.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

# Force UTF-8 console output so Chinese text survives Windows pipes.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("KB_ROOT") or _DEFAULT_ROOT).resolve()
DB_PATH = Path(os.environ.get("KB_DB", ROOT / "database" / "knowledge.db"))
MIGRATIONS = ROOT / "database" / "migrations"
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"
BACKUPS = ROOT / "backups"

CATEGORIES = [
    "plugin", "skill", "mcp", "command", "script", "workflow", "prompt",
    "github_project", "troubleshooting", "software", "environment", "service", "other",
]

INSTALL_STATUS = ["not_installed", "installed", "partial", "unknown"]
VERIFY_STATUS = ["unverified", "verified", "failed", "not_applicable"]
LIFECYCLE = [
    "pending_verification", "verified", "in_use", "needs_update",
    "expired", "deprecated", "replaced", "verify_failed",
]
EVIDENCE = ["A", "B", "C", "D", "E"]


# --------------------------------------------------------------- utilities
def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def ensure_dirs() -> None:
    for p in (ROOT, DB_PATH.parent, MIGRATIONS, REPORTS, LOGS, BACKUPS):
        p.mkdir(parents=True, exist_ok=True)


def connect(readonly: bool = False) -> sqlite3.Connection:
    ensure_dirs()
    if readonly:
        uri = "file:%s?mode=ro" % str(DB_PATH).replace("\\", "/")
        con = sqlite3.connect(uri, uri=True, timeout=10)
    else:
        con = sqlite3.connect(str(DB_PATH), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    return con


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def migrate(verbose: bool = False) -> list[str]:
    """Apply pending SQL migrations. Backs up the DB before first structural change."""
    ensure_dirs()
    con = connect()
    applied = []
    try:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL,"
            " checksum TEXT NOT NULL, notes TEXT)"
        )
        con.commit()
        done = {r["version"] for r in con.execute("SELECT version FROM schema_migrations")}
        files = sorted(MIGRATIONS.glob("V*.sql"))
        pending = [f for f in files if f.stem.split("__")[0] not in done]
        if pending and DB_PATH.exists() and DB_PATH.stat().st_size > 0:
            backup("pre-migrate")
        for f in pending:
            version = f.stem.split("__")[0]
            sql = f.read_text(encoding="utf-8")
            con.executescript(sql)
            con.execute(
                "INSERT INTO schema_migrations(version, applied_at, checksum, notes) VALUES(?,?,?,?)",
                (version, now(), _checksum(sql), f.name),
            )
            con.commit()
            applied.append(version)
            if verbose:
                print("[migrate] applied", version)
        kv_set(con, "schema_version", (sorted(done | set(applied) | {f.stem.split("__")[0] for f in files})[-1] if files else "0"))
        con.commit()
    finally:
        con.close()
    return applied


def kv_set(con: sqlite3.Connection, key: str, value) -> None:
    con.execute(
        "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(value), now()),
    )


def kv_get(key: str, default=None):
    try:
        con = connect(readonly=True)
        try:
            row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            con.close()
    except sqlite3.Error:
        return default


# ------------------------------------------------------------------ secrets
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-\.]{16,}"), "openai/anthropic style key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "github token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws access key"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.\+/]{12,})"),
     "key=value credential"),
    (re.compile(r"(?i)eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "jwt"),
]
REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for rx, _label in SECRET_PATTERNS:
        def _sub(m):
            if m.lastindex and m.lastindex >= 2:
                key, val = m.group(1), m.group(2)
                # ignore code that merely *reads* a secret from the environment
                if any(tok in val.lower() for tok in ("environ", "getenv", "get(", "sys.", "self.")):
                    return m.group(0)
                return key + "=" + REDACTED
            return REDACTED
        out = rx.sub(_sub, out)
    return out


def scan_secrets(paths) -> list[dict]:
    """Return findings for a list of files/dirs. Never prints the secret itself."""
    findings = []
    if isinstance(paths, (str, Path)):
        paths = [paths]
    for p in paths:
        p = Path(p)
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
        for f in files:
            if f.stat().st_size > 4 * 1024 * 1024:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for rx, label in SECRET_PATTERNS:
                    m = rx.search(line)
                    if m and m.lastindex and m.lastindex >= 2 and any(
                            t in (m.group(2) or "").lower()
                            for t in ("environ", "getenv", "get(", "sys.", "self.")):
                        continue
                    if m:
                        findings.append({
                            "file": str(f),
                            "line": lineno,
                            "type": label,
                            "preview": redact(line.strip())[:160],
                        })
                        break
    return findings


# -------------------------------------------------------------- asset layer
_ASSET_FIELDS = [
    "name", "category", "summary", "use_cases", "source_type", "source_url",
    "docs_url", "version", "license", "install_method", "install_command",
    "config_method", "invoke_method", "runtime_env", "permissions",
    "security_risk", "rollback_method", "install_status", "verify_status",
    "lifecycle_status", "evidence_level", "confidence", "path",
    "last_verified_at", "last_checked_at", "usage_count", "notes",
]


def slugify(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", (name or "").strip().lower())
    return s.strip("-")[:60] or "asset"


def asset_id(category: str, name: str) -> str:
    return "%s:%s" % (category, slugify(name))


def _get(con: sqlite3.Connection, table: str, aid: str):
    return con.execute("SELECT * FROM %s WHERE id=?" % table, (aid,)).fetchone()


def reindex_asset(con: sqlite3.Connection, aid: str) -> None:
    row = _get(con, "assets", aid)
    con.execute("DELETE FROM assets_fts WHERE id=?", (aid,))
    if not row:
        return
    tags = " ".join(r["tag"] for r in con.execute(
        "SELECT tag FROM asset_tags WHERE asset_id=? ORDER BY tag", (aid,)))
    con.execute(
        "INSERT INTO assets_fts(id,name,category,summary,use_cases,invoke_method,notes,tags)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (aid, row["name"] or "", row["category"] or "", row["summary"] or "",
         row["use_cases"] or "", row["invoke_method"] or "", row["notes"] or "", tags),
    )


def upsert_asset(con: sqlite3.Connection, data: dict, tags=None, links=None, event=None) -> str:
    """Insert or merge an asset record. Returns the asset id.

    Only supplied keys are written on update, so partial updates never erase data.
    Secrets are redacted from every textual field before persisting.
    """
    category = data.get("category") or "other"
    name = (data.get("name") or "").strip()
    aid = data.get("id") or asset_id(category, name)
    existing = _get(con, "assets", aid) if aid else None
    if not name and existing:          # partial update by id
        name = existing["name"]
    if not name:
        raise ValueError("asset requires a name")
    if existing and existing["category"]:
        category = data.get("category") or existing["category"]
    if not existing:
        dup = con.execute("SELECT id FROM assets WHERE name=? AND category=?",
                          (name, category)).fetchone()
        if dup:
            aid = dup["id"]
            existing = _get(con, "assets", aid)
    payload = {}
    for k in _ASSET_FIELDS:
        if k in data and data[k] is not None:
            v = data[k]
            if isinstance(v, str):
                v = redact(v)
            payload[k] = v
    payload["category"] = category
    payload["name"] = name
    ts = now()
    if existing:
        sets = ", ".join("%s=?" % k for k in payload)
        con.execute("UPDATE assets SET %s, updated_at=? WHERE id=?" % sets,
                    list(payload.values()) + [ts, aid])
    else:
        payload.setdefault("install_status", "unknown")
        payload.setdefault("verify_status", "unverified")
        payload.setdefault("lifecycle_status", "pending_verification")
        payload.setdefault("evidence_level", "E")
        payload.setdefault("confidence", 0)
        cols = list(payload.keys()) + ["id", "created_at", "updated_at"]
        vals = list(payload.values()) + [aid, ts, ts]
        con.execute("INSERT INTO assets(%s) VALUES(%s)" % (
            ",".join(cols), ",".join("?" * len(cols))), vals)

    if tags is not None:
        con.execute("DELETE FROM asset_tags WHERE asset_id=?", (aid,))
        for t in {t.strip().lower() for t in tags if t and t.strip()}:
            con.execute("INSERT OR IGNORE INTO asset_tags(asset_id,tag) VALUES(?,?)", (aid, t))

    if links:
        for dst, relation, note in links:
            if not dst or dst == aid:
                continue
            if not _get(con, "assets", dst):
                continue
            con.execute(
                "INSERT OR IGNORE INTO asset_links(src_id,dst_id,relation,note) VALUES(?,?,?,?)",
                (aid, dst, relation or "related", redact(note or "")))

    if event:
        add_event(con, aid, event[0], event[1] if len(event) > 1 else "", "codex")
    reindex_asset(con, aid)
    con.commit()
    return aid


def add_event(con, aid: str, event: str, detail: str = "", actor: str = "codex") -> None:
    con.execute(
        "INSERT INTO asset_events(asset_id,event,detail,actor,happened_at,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (aid, event, redact(detail or ""), actor, now(), now()))


def add_verification(con, aid: str, check_name: str, command: str, result: str, output: str = ""):
    con.execute(
        "INSERT INTO verifications(asset_id,check_name,command,result,output,verified_at)"
        " VALUES(?,?,?,?,?,?)",
        (aid, check_name, redact(command or ""), result, redact((output or "")[:4000]), now()))
    if result == "pass":
        con.execute(
            "UPDATE assets SET verify_status='verified', lifecycle_status='verified',"
            " last_verified_at=?, last_checked_at=?, updated_at=? WHERE id=?",
            (now(), now(), now(), aid))
    elif result == "fail":
        con.execute(
            "UPDATE assets SET verify_status='failed', lifecycle_status='verify_failed',"
            " last_checked_at=?, updated_at=? WHERE id=?", (now(), now(), aid))
    con.commit()


def set_status(con, aid: str, **kw) -> None:
    allowed = {"install_status", "verify_status", "lifecycle_status", "evidence_level",
               "confidence", "last_checked_at", "last_verified_at", "notes", "version", "path"}
    payload = {k: v for k, v in kw.items() if k in allowed}
    if not payload:
        return
    sets = ", ".join("%s=?" % k for k in payload)
    con.execute("UPDATE assets SET %s, updated_at=? WHERE id=?" % sets,
                list(payload.values()) + [now(), aid])
    con.commit()


def get_asset(aid: str):
    con = connect(readonly=True)
    try:
        row = _get(con, "assets", aid)
        if not row:
            return None
        d = dict(row)
        d["tags"] = [r["tag"] for r in con.execute(
            "SELECT tag FROM asset_tags WHERE asset_id=? ORDER BY tag", (aid,))]
        d["links_out"] = [dict(r) for r in con.execute(
            "SELECT dst_id,relation,note FROM asset_links WHERE src_id=? ORDER BY relation", (aid,))]
        d["links_in"] = [dict(r) for r in con.execute(
            "SELECT src_id,relation,note FROM asset_links WHERE dst_id=? ORDER BY relation", (aid,))]
        d["events"] = [dict(r) for r in con.execute(
            "SELECT event,detail,actor,happened_at FROM asset_events WHERE asset_id=?"
            " ORDER BY id DESC LIMIT 30", (aid,))]
        d["verifications"] = [dict(r) for r in con.execute(
            "SELECT check_name,command,result,output,verified_at FROM verifications"
            " WHERE asset_id=? ORDER BY id DESC LIMIT 20", (aid,))]
        return d
    finally:
        con.close()


# ------------------------------------------------------------------- search
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-\.\+#]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokens(text: str) -> list[str]:
    """Latin words + CJK character bigrams (works without a zh segmenter)."""
    if not text:
        return []
    text = text.lower()
    toks = _LATIN_RE.findall(text)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _vector(text: str) -> dict:
    v = {}
    for t in _tokens(text):
        v[t] = v.get(t, 0) + 1
    return v


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return num / (na * nb) if na and nb else 0.0


def _load_synonyms() -> dict:
    """读取 config/synonyms.yaml（极简 YAML 子集解析，零依赖）。"""
    p = ROOT / "config" / "synonyms.yaml"
    out = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            vals = [x.strip().strip("\"'") for x in val.strip().strip("[]").split(",")]
            vals = [v for v in vals if v]
            if key and vals:
                out[key.lower()] = vals
    except Exception:
        pass
    return out


_SYNONYMS = None


def expand_query(q: str) -> list:
    """返回 [原查询, 展开词...]，用于多路召回。"""
    global _SYNONYMS
    if _SYNONYMS is None:
        _SYNONYMS = _load_synonyms()
    variants = [q]
    ql = q.strip().lower()
    for k, vs in _SYNONYMS.items():
        if k == ql or (len(k) >= 2 and k in ql):
            variants.extend(v for v in vs if v not in variants)
    return variants[:5]


def _fts_safe(q: str) -> str:
    return '"' + q.replace('"', ' ') + '"'


def _rank(con, term: str, category, cat_clause, cat_args, add, weight, orig) -> None:
    """对单个检索词跑一轮多路召回，按 weight 加权。"""
    # 1) exact name
    for row in con.execute("SELECT a.* FROM assets a WHERE lower(a.name)=lower(?)" + cat_clause,
                           [term] + cat_args):
        add(row, 100 * weight, "名称完全匹配" if term == orig else "同义词精确匹配")
    # 2) name contains
    for row in con.execute("SELECT a.* FROM assets a WHERE a.name LIKE ?" + cat_clause,
                           ["%" + term + "%"] + cat_args):
        add(row, 40 * weight, "名称包含" if term == orig else "同义词包含")
    # 3) tags
    for row in con.execute(
            "SELECT a.* FROM assets a JOIN asset_tags t ON t.asset_id=a.id"
            " WHERE lower(t.tag)=lower(?)" + cat_clause, [term] + cat_args):
        add(row, 35 * weight, "标签匹配")
    # 4) FTS5 trigram
    if len(term) >= 3:
        try:
            for row in con.execute(
                    "SELECT a.* FROM assets_fts f JOIN assets a ON a.id=f.id"
                    " WHERE assets_fts MATCH ?" + cat_clause, [_fts_safe(term)] + cat_args):
                add(row, 25 * weight, "全文索引" if term == orig else "同义词全文")
        except sqlite3.Error:
            pass
    # 5) LIKE over descriptive fields
    like = "%" + term + "%"
    for row in con.execute(
            "SELECT a.* FROM assets a WHERE (a.summary LIKE ? OR a.use_cases LIKE ?"
            " OR a.notes LIKE ? OR a.invoke_method LIKE ? OR a.path LIKE ?)" + cat_clause,
            [like, like, like, like, like] + cat_args):
        add(row, 12 * weight, "描述包含" if term == orig else "同义词描述")
    # 6) TF-IDF cosine
    qv = _vector(term)
    for row in con.execute(
            "SELECT a.*, (SELECT group_concat(tag,' ') FROM asset_tags"
            " WHERE asset_id=a.id) AS tagstr FROM assets a WHERE 1=1" + cat_clause, cat_args):
        doc = " ".join(filter(None, [row["name"], row["summary"], row["use_cases"],
                                     row["notes"], row["invoke_method"],
                                     row["category"], row["tagstr"]]))
        sim = _cosine(qv, _vector(doc))
        if sim > 0.12:
            add(row, sim * 30 * weight, "语义相似 %.2f" % sim)


def search(query: str, category: str = None, limit: int = 20,
           include_history: bool = False) -> list[dict]:
    """混合检索：精确 → 标签 → FTS5 → LIKE → TF-IDF，附生命周期加权与命中原因。"""
    q = (query or "").strip()
    if not q:
        return []
    variants = expand_query(q)
    con = connect(readonly=True)
    try:
        cand: dict[str, dict] = {}

        def add(row, score, why):
            aid = row["id"]
            item = cand.setdefault(aid, {"asset": dict(row), "score": 0.0, "why": []})
            item["score"] += score
            if why not in item["why"]:
                item["why"].append(why)

        cat_clause = " AND a.category=?" if category else ""
        cat_args = [category] if category else []

        for i, term in enumerate(variants):
            _rank(con, term, category, cat_clause, cat_args, add,
                  1.0 if i == 0 else 0.55, q)

        # 生命周期与使用度加权
        prio = {"in_use": 12, "verified": 10, "pending_verification": 2,
                "needs_update": -4, "expired": -10, "deprecated": -14,
                "replaced": -14, "verify_failed": -8}
        for item in cand.values():
            a = item["asset"]
            item["score"] += prio.get(a["lifecycle_status"], 0)
            if a["install_status"] == "installed":
                item["score"] += 8
            item["score"] += min(a["usage_count"] or 0, 20) * 0.5

        out = []
        for item in sorted(cand.values(), key=lambda x: -x["score"])[:limit]:
            a = item["asset"]
            out.append({
                "id": a["id"], "name": a["name"], "category": a["category"],
                "summary": a["summary"], "lifecycle": a["lifecycle_status"],
                "install": a["install_status"], "verify": a["verify_status"],
                "path": a["path"], "invoke": a["invoke_method"],
                "score": round(item["score"], 2), "why": item["why"],
            })

        if include_history and len(out) < limit:
            seen = {o["id"] for o in out}
            for row in _search_history(con, q, limit - len(out)):
                if row["id"] not in seen:
                    out.append(row)
        return out
    finally:
        con.close()


def _search_history(con, q: str, limit: int) -> list[dict]:
    rows = []
    like = "%" + q + "%"
    for r in con.execute(
            "SELECT id,name,detail,thread_title,ts FROM history_items"
            " WHERE name LIKE ? OR detail LIKE ? ORDER BY ts DESC LIMIT ?",
            [like, like, max(1, limit) * 3]):
        nm = (r["name"] or "").strip()
        if len(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]", nm)) < 2:
            continue
        rows.append({"id": "history:%s" % r["id"], "name": nm,
                     "category": "history", "summary": (r["detail"] or "")[:160],
                     "lifecycle": "history", "install": "unknown", "verify": "unverified",
                     "path": None, "invoke": None, "score": 8.0,
                     "why": ["历史记录 %s" % (r["ts"] or "")]})
        if len(rows) >= limit:
            break
    return rows


def build_context(prompt: str = "", limit: int = 5, max_chars: int = 1500) -> str:
    """Compact knowledge-base context block injected into the model prompt."""
    stats = None
    try:
        con = connect(readonly=True)
        try:
            stats = con.execute(
                "SELECT COUNT(*) n, SUM(category='skill') s, SUM(category='mcp') m,"
                " SUM(lifecycle_status IN ('verified','in_use')) v FROM assets").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return "[KB] 知识库不可用：database/knowledge.db 缺失或损坏，请运行 kb.py doctor。"

    lines = []
    if stats and stats["n"]:
        lines.append("[KB] 全局知识库 D:\\Codex-Knowledge-Base 已加载：资产 %d 项"
                     "（skills %d / mcp %d / 已验证 %d）。" % (
                         stats["n"] or 0, stats["s"] or 0, stats["m"] or 0, stats["v"] or 0))
    else:
        lines.append("[KB] 知识库为空，请先运行 scripts/kb.py init 与 import-history。")

    if prompt:
        try:
            hits = search(prompt, limit=limit)
        except Exception:
            hits = []
        if hits:
            lines.append("与本任务相关的历史资产（按优先级，复用前必须实测当前环境）：")
            for h in hits:
                state = "%s/%s" % (h["install"], h["verify"])
                lines.append("- %s [%s] %s | %s" % (
                    h["name"], h["category"], state,
                    (h["summary"] or "")[:90].replace("\n", " ")))
    lines.append("规则：先查 KB → 已安装且验证过则直接复用 → 未安装先确认版本兼容 → "
                 "KB 无方案再搜索 GitHub → 仍无则原创，并记录搜索证据。")
    text = "\n".join(lines)
    return text[:max_chars]


# ------------------------------------------------------------ ops: backup
def backup(tag: str = "manual", keep: int = 30) -> Path:
    import shutil
    import zipfile
    ensure_dirs()
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = BACKUPS / ("kb-%s-%s.zip" % (ts, re.sub(r"[^0-9A-Za-z_\-]", "", tag or "x")))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            tmp = DB_PATH.with_suffix(".backup-tmp")
            try:
                src = sqlite3.connect(str(DB_PATH))
                dst = sqlite3.connect(str(tmp))
                src.backup(dst)
                dst.close()
                src.close()
                z.write(tmp, "database/knowledge.db")
            finally:
                if tmp.exists():
                    tmp.unlink()
        for name in ("AGENTS.md", "README.md", "CHANGELOG.md"):
            p = ROOT / name
            if p.exists():
                z.write(p, name)
        for p in sorted((ROOT / "config").glob("*")):
            if p.is_file():
                z.write(p, "config/" + p.name)
        for p in sorted((ROOT / "database" / "migrations").glob("*.sql")):
            z.write(p, "database/migrations/" + p.name)
    # prune
    existing = sorted(BACKUPS.glob("kb-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in existing[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return out


def restore(zip_path: str, force: bool = False) -> str:
    import zipfile
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(zip_path)
    if DB_PATH.exists() and not force:
        raise RuntimeError("数据库已存在；如需覆盖请加 --force（会先自动备份）")
    if DB_PATH.exists():
        backup("pre-restore")
    with zipfile.ZipFile(zp) as z:
        for member in z.namelist():
            if member.startswith("database/knowledge.db"):
                DB_PATH.write_bytes(z.read(member))
            elif member.startswith(("config/", "database/migrations/")) or "/" not in member:
                target = ROOT / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(member))
    return str(DB_PATH)


# --------------------------------------------------------- docs indexing
def index_docs() -> int:
    con = connect()
    n = 0
    try:
        con.execute("DELETE FROM docs")
        con.execute("DELETE FROM docs_fts")
        for p in sorted(ROOT.rglob("*.md")):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(("logs/", "backups/", "exports/")):
                continue
            try:
                body = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            title = rel
            for line in body.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            con.execute("INSERT INTO docs(path,title,category,body,updated_at) VALUES(?,?,?,?,?)",
                        (rel, title, rel.split("/")[0], body, now()))
            con.execute("INSERT INTO docs_fts(path,title,body) VALUES(?,?,?)", (rel, title, body))
            n += 1
        con.commit()
    finally:
        con.close()
    return n


def search_docs(q: str, limit: int = 20) -> list[dict]:
    out = []
    con = connect(readonly=True)
    try:
        if len(q) >= 3:
            try:
                for r in con.execute(
                        "SELECT d.path,d.title,category, substr(d.body,1,300) snippet"
                        " FROM docs_fts f JOIN docs d ON d.path=f.path"
                        " WHERE docs_fts MATCH ? LIMIT ?", [_fts_safe(q), limit]):
                    out.append(dict(r))
            except sqlite3.Error:
                pass
        if len(out) < limit:
            like = "%" + q + "%"
            for r in con.execute(
                    "SELECT path,title,category,substr(body,1,300) snippet FROM docs"
                    " WHERE body LIKE ? LIMIT ?", [like, limit - len(out)]):
                if r["path"] not in {o["path"] for o in out}:
                    out.append(dict(r))
    finally:
        con.close()
    return out


# ------------------------------------------------------------ stats/health
def stats() -> dict:
    con = connect(readonly=True)
    try:
        g = lambda sql, *a: con.execute(sql, a).fetchone()[0]
        d = {
            "assets": g("SELECT COUNT(*) FROM assets"),
            "by_category": {r["category"]: r["n"] for r in con.execute(
                "SELECT category, COUNT(*) n FROM assets GROUP BY category ORDER BY n DESC")},
            "verified": g("SELECT COUNT(*) FROM assets WHERE verify_status='verified'"),
            "installed": g("SELECT COUNT(*) FROM assets WHERE install_status='installed'"),
            "unverified": g("SELECT COUNT(*) FROM assets WHERE verify_status='unverified'"),
            "expired": g("SELECT COUNT(*) FROM assets WHERE lifecycle_status IN ('expired','deprecated','replaced')"),
            "failed": g("SELECT COUNT(*) FROM assets WHERE verify_status='failed'"),
            "history_items": g("SELECT COUNT(*) FROM history_items"),
            "commands": g("SELECT COUNT(*) FROM commands"),
            "errors": g("SELECT COUNT(*) FROM errors"),
            "docs": g("SELECT COUNT(*) FROM docs"),
            "events": g("SELECT COUNT(*) FROM asset_events"),
            "last_health": kv_get("last_health_check", "从未") ,
            "schema_version": kv_get("schema_version", "?"),
            "recent_events": [dict(r) for r in con.execute(
                "SELECT e.asset_id, a.name, e.event, e.detail, e.happened_at FROM asset_events e"
                " LEFT JOIN assets a ON a.id=e.asset_id ORDER BY e.id DESC LIMIT 12")],
            "recent_history": [dict(r) for r in con.execute(
                "SELECT kind,name,thread_title,ts FROM history_items ORDER BY id DESC LIMIT 12")],
        }
        return d
    finally:
        con.close()


def integrity() -> dict:
    res = {"db_exists": DB_PATH.exists(), "quick_check": None, "fts_ok": None, "schema": None}
    if not DB_PATH.exists():
        return res
    con = connect(readonly=True)
    try:
        res["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        res["schema"] = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        try:
            con.execute("SELECT count(*) FROM assets_fts WHERE assets_fts MATCH '\"codex\"'").fetchone()
            res["fts_ok"] = True
        except sqlite3.Error as e:
            res["fts_ok"] = str(e)
    except sqlite3.Error as e:
        res["quick_check"] = "error: %s" % e
    finally:
        con.close()
    return res

# ------------------------------------------------------- dependency chains
def dependency_chain(aid: str, direction: str = "down", depth: int = 6) -> list[dict]:
    """Return the transitive dependency graph around an asset.

    direction="down"  : what this asset depends on / uses
    direction="up"    : what depends on this asset (reverse impact analysis)
    """
    if direction not in ("down", "up"):
        raise ValueError("direction must be 'down' or 'up'")
    col_from, col_to = ("src_id", "dst_id") if direction == "down" else ("dst_id", "src_id")
    sql = """
    WITH RECURSIVE chain(id, relation, lvl, path) AS (
        SELECT dst_id, relation, 1, src_id || '>' || dst_id
          FROM asset_links WHERE {f} = ?
        UNION ALL
        SELECT CASE WHEN ? = 'down' THEN l.dst_id ELSE l.src_id END,
               l.relation, c.lvl + 1,
               c.path || '>' || CASE WHEN ? = 'down' THEN l.dst_id ELSE l.src_id END
          FROM asset_links l
          JOIN chain c ON CASE WHEN ? = 'down' THEN l.src_id = c.id ELSE l.dst_id = c.id END
         WHERE c.lvl < ? AND instr(c.path, CASE WHEN ? = 'down' THEN l.dst_id ELSE l.src_id END) = 0
    )
    SELECT c.id, c.relation, c.lvl, a.name, a.category, a.install_status, a.verify_status
      FROM chain c LEFT JOIN assets a ON a.id = c.id
     ORDER BY c.lvl, a.name
    """.format(f=col_from)
    con = connect(readonly=True)
    try:
        args = [aid, direction, direction, direction, depth, direction]
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def add_link(con, src: str, dst: str, relation: str = "depends_on", note: str = "") -> bool:
    if not _get(con, "assets", src) or not _get(con, "assets", dst):
        return False
    con.execute("INSERT OR IGNORE INTO asset_links(src_id,dst_id,relation,note)"
                " VALUES(?,?,?,?)", (src, dst, relation, redact(note or "")))
    con.commit()
    return True

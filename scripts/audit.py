#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit.py -- version & maintenance audit (spec section 10.3 / 11.5).

For every github_project asset: query live metadata (archived / pushed_at / stars /
license / latest release) and update lifecycle status.
For every software asset with a probe command: re-probe the version and record drift.

Nothing is uninstalled or replaced automatically; the audit only marks状态 and writes
reports/version-audit.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402


def gh_api(path: str):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or "").strip()[:200]
    try:
        return json.loads(r.stdout), None
    except Exception as e:
        return None, str(e)


def _probe_repo(slug: str) -> dict:
    """单个仓库：一次 GraphQL 拿到 stars/license/pushedAt/archived/latestRelease。"""
    q = ('{repository(owner:"%s",name:"%s"){stargazersCount isArchived pushedAt '
         'licenseInfo{spdxId} releases(last:1){nodes{tagName}}}}') % tuple(slug.split("/", 1))
    r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + q],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"slug": slug, "error": (r.stderr or "")[:160]}
    try:
        repo = (json.loads(r.stdout).get("data") or {}).get("repository")
    except Exception as e:
        return {"slug": slug, "error": str(e)[:120]}
    if not repo:
        return {"slug": slug, "error": "not found"}
    rel = ((repo.get("releases") or {}).get("nodes") or [{}])
    return {
        "slug": slug,
        "stars": repo.get("stargazersCount"),
        "archived": bool(repo.get("isArchived")),
        "pushed": repo.get("pushedAt") or "",
        "license": ((repo.get("licenseInfo") or {}) or {}).get("spdxId") or "",
        "release": (rel[0] if rel else {}).get("tagName") or "",
    }


def audit_github(con, limit=80, workers=8):
    rows = list(con.execute(
        "SELECT id,name,source_url,version,lifecycle_status FROM assets"
        " WHERE category='github_project' AND source_url LIKE 'https://github.com/%'"
        " ORDER BY usage_count DESC LIMIT ?", (limit,)))
    slugs = []
    for r in rows:
        slug = r["source_url"].split("github.com/", 1)[1].strip("/").strip()
        if slug.count("/") == 1:
            slugs.append((r, slug))
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_repo, s): slug for _, s in slugs}
        for f in as_completed(futs):
            res = f.result()
            results[res["slug"]] = res

    out = []
    for r, slug in slugs:
        d = results.get(slug) or {"error": "no result"}
        if d.get("error"):
            out.append({"name": r["name"], "state": "error", "detail": d["error"]})
            continue
        pushed, archived, lic = d["pushed"], d["archived"], d["license"]
        stars, tag = d["stars"], d["release"]
        age_days = 9999
        if pushed:
            try:
                dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - dt).days
            except Exception:
                pass
        if archived:
            life = "deprecated"
        elif age_days > 540:
            life = "expired"
        elif age_days > 365:
            life = "needs_update"
        else:
            life = r["lifecycle_status"] or "pending_verification"
        notes = ("stars=%s; license=%s; pushed=%s; 距今天数=%s; release=%s"
                 % (stars, lic or "未知", pushed[:10], age_days, tag or "无"))
        con.execute(
            "UPDATE assets SET version=?, lifecycle_status=?, last_checked_at=?,"
            " license=COALESCE(NULLIF(license,''),?), notes=?, updated_at=? WHERE id=?",
            (tag or r["version"] or "", life, kb.now(), lic, notes, kb.now(), r["id"]))
        if life in ("expired", "deprecated"):
            kb.add_event(con, r["id"], "deprecated" if archived else "noted", "审计：%s" % notes)
        out.append({"name": r["name"], "state": life, "detail": notes,
                    "age": age_days, "stars": stars, "license": lic})
    con.commit()
    return out


SOFT_PROBES = {
    "git": "git --version", "gh": "gh --version", "python": "python --version",
    "node": "node --version", "npm": "npm --version", "docker": "docker --version",
    "ollama": "ollama --version", "rg": "rg --version", "winget": "winget --version",
}


def audit_software(con):
    changed = []
    for name, cmd in SOFT_PROBES.items():
        aid = "software:%s" % kb.slugify(name)
        row = con.execute("SELECT id,version FROM assets WHERE id=?", (aid,)).fetchone()
        if not row:
            continue
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20,
                               encoding="utf-8", errors="replace")
            newer = (r.stdout or r.stderr or "").strip().splitlines()
            newer = newer[0][:120] if newer else ""
        except Exception:
            continue
        old = row["version"] or ""
        if newer and newer != old:
            con.execute("UPDATE assets SET version=?, last_checked_at=?, updated_at=? WHERE id=?",
                        (newer, kb.now(), kb.now(), aid))
            kb.add_event(con, aid, "updated", "版本变化：%s -> %s" % (old or "未知", newer))
            changed.append((name, old or "未知", newer))
    con.commit()
    return changed


def main():
    kb.migrate()
    con = kb.connect()
    try:
        print("[audit] 正在核查 GitHub 项目（gh api）…")
        gh = audit_github(con)
        print("[audit] 已核查 %d 个仓库" % len(gh))
        print("[audit] 正在复核本地软件版本…")
        sw = audit_software(con)
        for n, o, v in sw:
            print("  版本变化 %-10s %s -> %s" % (n, o, v))
        if not sw:
            print("  无版本变化")
        lines = ["# 版本与维护状态审计", "", "- 生成时间：%s" % kb.now(),
                 "- 检查仓库：%d 个（gh api，含归档/最后推送/星标/许可证/最新 release）" % len(gh),
                 "- 本地软件版本变化：%d 项" % len(sw), "",
                 "## GitHub 项目", "",
                 "| 仓库 | 状态 | 星标 | 许可证 | 距最后推送 | 说明 |", "|---|---|---|---|---|---|"]
        for g in gh:
            age = g.get("age")
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                g["name"], g["state"], g.get("stars", ""), g.get("license", "") or "-",
                ("%d 天" % age) if isinstance(age, int) and age < 9999 else "-",
                (g.get("detail") or "").replace("|", "/")[:110]))
        lines += ["", "## 本地软件", ""]
        lines += ["- `%s` %s → %s" % (n, o, v) for n, o, v in sw] or ["- 无变化"]
        lines += ["", "## 规则", "",
                  "- 归档仓库 → `deprecated`；超过 540 天无推送 → `expired`；超过 365 天 → `needs_update`。",
                  "- 审计只改状态，不卸载、不替换任何工具；是否替换由人工决定。"]
        p = kb.REPORTS / "version-audit.md"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("[audit] 报告:", p)
    finally:
        con.close()


if __name__ == "__main__":
    main()

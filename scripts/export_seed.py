#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_seed.py -- dump curated assets to database/seed.json (small, Git-friendly).

Division of labour
------------------
* seed.json      : curated assets + tags + links + verification summaries  -> versioned in Git
* knowledge.db   : full local database (8k+ history rows)                  -> local only, rebuildable
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402

FIELDS = ["id", "name", "category", "summary", "use_cases", "source_type", "source_url",
          "docs_url", "version", "license", "install_method", "install_command",
          "config_method", "invoke_method", "runtime_env", "permissions", "security_risk",
          "rollback_method", "install_status", "verify_status", "lifecycle_status",
          "evidence_level", "confidence", "path", "last_verified_at", "last_checked_at",
          "usage_count", "notes"]


def export(path: Path = None) -> Path:
    path = path or (kb.ROOT / "database" / "seed.json")
    con = kb.connect(readonly=True)
    try:
        assets = []
        for r in con.execute("SELECT * FROM assets ORDER BY category, name"):
            d = {k: r[k] for k in FIELDS if k in r.keys()}
            d["tags"] = [t["tag"] for t in con.execute(
                "SELECT tag FROM asset_tags WHERE asset_id=? ORDER BY tag", (r["id"],))]
            d["links"] = [{"dst": l["dst_id"], "relation": l["relation"], "note": l["note"]}
                          for l in con.execute(
                              "SELECT dst_id,relation,note FROM asset_links WHERE src_id=?", (r["id"],))]
            assets.append(d)
        commands = [dict(r) for r in con.execute(
            "SELECT signature,tool,verb,uses,ok_count,fail_count,risk,last_seen FROM commands"
            " ORDER BY uses DESC LIMIT 400")]
        errors = [dict(r) for r in con.execute(
            "SELECT signature,occurrences,first_seen,last_seen FROM errors"
            " ORDER BY occurrences DESC LIMIT 150")]
    finally:
        con.close()
    payload = {"version": 1, "generated_at": kb.now(), "assets": assets,
               "top_commands": commands, "top_errors": errors}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load(path: Path = None) -> int:
    path = path or (kb.ROOT / "database" / "seed.json")
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    con = kb.connect()
    n = 0
    try:
        for a in data.get("assets", []):
            tags = a.pop("tags", [])
            links = [(l["dst"], l["relation"], l.get("note")) for l in a.pop("links", [])]
            kb.upsert_asset(con, a, tags=tags, links=links,
                            event=("discovered", "seed.json 导入"))
            n += 1
        for c in data.get("top_commands", []):
            con.execute("INSERT OR IGNORE INTO commands(signature,tool,verb,uses,ok_count,"
                        "fail_count,risk,last_seen) VALUES(?,?,?,?,?,?,?,?)",
                        (c["signature"], c["tool"], c["verb"], c["uses"], c["ok_count"],
                         c["fail_count"], c["risk"], c["last_seen"]))
        for e in data.get("top_errors", []):
            con.execute("INSERT OR IGNORE INTO errors(signature,occurrences,first_seen,last_seen)"
                        " VALUES(?,?,?,?)", (e["signature"], e["occurrences"],
                                             e["first_seen"], e["last_seen"]))
        con.commit()
    finally:
        con.close()
    return n


if __name__ == "__main__":
    if "--load" in sys.argv:
        print("已从 seed 导入资产:", load())
    else:
        p = export()
        print("已导出:", p, "%.1f KB" % (p.stat().st_size / 1024))

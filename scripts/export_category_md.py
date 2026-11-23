#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_category_md.py -- mirror each asset category into browsable Markdown."""
from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402

MAP = {
    "skill": "skills",
    "plugin": "plugins",
    "mcp": "mcp",
    "command": "commands",
    "script": "scripts-md",
    "workflow": "workflows",
    "prompt": "prompts",
    "github_project": "github-projects",
    "troubleshooting": "troubleshooting",
    "software": "software",
    "environment": "environments",
}


LABEL = {
    "skill": "Skills 技能包索引", "plugin": "插件索引", "mcp": "MCP 服务索引",
    "command": "命令索引", "script": "脚本索引", "workflow": "工作流索引",
    "prompt": "提示词索引", "github_project": "GitHub 项目索引",
    "troubleshooting": "故障与解决方案索引", "software": "软件索引",
    "environment": "环境与依赖索引",
}


def esc(v):
    return str(v or "").replace("|", "\\|").replace("\n", " ")[:140]


def main():
    con = kb.connect(readonly=True)
    total = 0
    try:
        for cat, folder in MAP.items():
            rows = [dict(r) for r in con.execute(
                "SELECT name,summary,install_status,verify_status,lifecycle_status,"
                "evidence_level,version,path FROM assets WHERE category=? ORDER BY name", (cat,))]
            d = kb.ROOT / folder
            d.mkdir(parents=True, exist_ok=True)
            lines = ["# %s" % LABEL.get(cat, folder), "",
                     "由 `scripts/export_category_md.py` 从 `database/knowledge.db` 自动生成，请勿手改。", "",
                     "共 **%d** 项。" % len(rows), ""]
            if rows:
                lines += ["| 名称 | 安装 | 验证 | 生命周期 | 证据 | 版本 | 说明 |",
                          "|---|---|---|---|---|---|---|"]
                for r in rows:
                    lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                        esc(r["name"]), esc(r["install_status"]), esc(r["verify_status"]),
                        esc(r["lifecycle_status"]), esc(r["evidence_level"]),
                        esc(r["version"]), esc(r["summary"])))
                lines += ["", "## 路径明细", ""]
                for r in rows:
                    if r["path"]:
                        lines.append("- `%s` — %s" % (r["path"], esc(r["name"])))
            p = d / "index.md"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            total += len(rows)
    finally:
        con.close()
    n = kb.index_docs()
    print("[category-md] 已生成 %d 个分类索引，覆盖 %d 项资产；文档索引 %d 篇" % (len(MAP), total, n))


if __name__ == "__main__":
    main()

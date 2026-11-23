#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_projects.py -- 扫描已有项目并评估知识库接入状态。

全局 hooks + 全局 AGENTS.md 已覆盖所有目录，项目级文件不是必需。
本脚本只盘点项目、记录接入方式与最后提交，并输出可选片段，不修改业务仓库。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402

def _roots():
    """公开版：默认扫描知识库自身与当前目录，可用 KB_SCAN_ROOTS 覆盖（分号分隔）。"""
    env = os.environ.get("KB_SCAN_ROOTS", "").strip()
    if env:
        return [Path(x) for x in re.split(r"[;]", env) if x.strip()]
    here = Path(__file__).resolve().parent.parent
    return [here, Path.cwd()]


ROOTS = _roots()

SKIP = {"node_modules", ".git", "venv", ".venv", "dist", "build", "__pycache__"}


def git_info(p: Path) -> dict:
    out = {"is_git": (p / ".git").exists(), "branch": "", "remote": "", "last_commit": ""}
    if not out["is_git"]:
        return out
    try:
        out["branch"] = subprocess.run(["git", "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD"],
                                       capture_output=True, text=True).stdout.strip()
        out["remote"] = subprocess.run(["git", "-C", str(p), "remote", "get-url", "origin"],
                                       capture_output=True, text=True).stdout.strip()
        ts = subprocess.run(["git", "-C", str(p), "log", "-1", "--format=%cI"],
                            capture_output=True, text=True).stdout.strip()
        out["last_commit"] = ts[:19].replace("T", " ")
    except Exception:
        pass
    return out


def discover(base: Path) -> list:
    found = []
    if not base.exists():
        return found
    if (base / ".git").exists() or (base / "AGENTS.md").exists():
        found.append(base)
    if base.is_dir():
        for p in base.iterdir():
            if p.is_dir() and p.name not in SKIP:
                if (p / ".git").exists() or (p / "AGENTS.md").exists():
                    found.append(p)
    return found


SNIPPET = "\n".join([
    "<!-- 知识库接入（可选，最小化） -->",
    "## 全局知识库",
    "",
    "本项目已由 Codex 全局 hooks 覆盖（SessionStart 自动注入 `D:\\Codex-Knowledge-Base` 摘要）。",
    "检索历史工具与命令：`python D:\\Codex-Knowledge-Base\\scripts\\kb.py search \"关键词\" --history`。",
    "",
    "修改本项目时仍以本文件其余规则为准，全局规则不覆盖项目特殊要求。",
])


def main():
    kb.migrate()
    projects = []
    seen = set()
    for r in ROOTS:
        for p in discover(r):
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            gi = git_info(p)
            agents = p / "AGENTS.md"
            size = agents.stat().st_size if agents.exists() else 0
            mtime = ""
            if agents.exists():
                mtime = datetime.fromtimestamp(agents.stat().st_mtime).strftime("%Y-%m-%d")
            parent = str(p.parent)
            label = p.name
            if parent.rstrip("\\/").lower() in (str(Path.home()), str(Path.home().anchor)):
                label = "%s (%s)" % (p.name, parent)
            projects.append({"path": str(p), "name": label, **gi,
                             "agents": agents.exists(), "agents_bytes": size,
                             "agents_mtime": mtime})

    con = kb.connect()
    try:
        for pr in projects:
            mode = "全局 hooks（无需项目文件）" if not pr["agents"] else "全局 hooks + 项目 AGENTS.md"
            kb.upsert_asset(con, {
                "id": "environment:project-%s-%s" % (
                    kb.slugify(pr["name"]),
                    hashlib.sha1(pr["path"].lower().encode()).hexdigest()[:6]),
                "name": pr["name"], "category": "environment",
                "summary": "已有项目：%s" % pr["path"],
                "use_cases": "在该目录下的 Codex 会话自动继承知识库上下文",
                "source_type": "local", "path": pr["path"],
                "runtime_env": ("git:%s %s" % (pr["branch"], pr["remote"])) if pr["is_git"] else "非 git 目录",
                "invoke_method": mode,
                "install_status": "installed",
                "verify_status": "verified",
                "lifecycle_status": "in_use",
                "evidence_level": "A", "confidence": 90,
                "last_verified_at": kb.now(), "last_checked_at": kb.now(),
                "notes": "AGENTS.md=%s(%dB)；最后提交=%s" % (
                    "有" if pr["agents"] else "无", pr["agents_bytes"], pr["last_commit"] or "-"),
            }, tags=["project", "integration"] + (["git"] if pr["is_git"] else []),
               event=("discovered", "项目扫描"))
        con.commit()
    finally:
        con.close()

    lines = ["# 项目接入状况", "", "- 生成时间：%s" % kb.now(),
             "- 结论：全局 `SessionStart` hook 已覆盖所有目录，**无需逐个修改业务项目**。",
             "- 项目级 `AGENTS.md` 仅在需要追加项目特有约束时使用，且必须保留原有内容。", "",
             "| 项目 | 路径 | Git | 分支 | AGENTS.md | 接入方式 | 最后提交 |",
             "|---|---|---|---|---|---|---|"]
    for pr in projects:
        lines.append("| %s | `%s` | %s | %s | %s | %s | %s |" % (
            pr["name"], pr["path"], "Y" if pr["is_git"] else "-", pr["branch"] or "-",
            ("有 %dB" % pr["agents_bytes"]) if pr["agents"] else "无",
            "全局 hooks" if not pr["agents"] else "全局 + 项目",
            pr["last_commit"] or "-"))
    lines += ["", "## 可选：项目级接入片段", "",
              "如需显式声明，把下面这段**追加**到该项目 AGENTS.md 末尾（不要覆盖原内容）：",
              "", "```markdown", SNIPPET, "```"]
    p = kb.REPORTS / "project-integration.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (kb.ROOT / "docs" / "project-agents-snippet.md").write_text(SNIPPET + "\n", encoding="utf-8")

    print("[projects] 发现 %d 个项目" % len(projects))
    for pr in projects:
        print("  %-26s git=%-5s AGENTS=%-5s %s" % (
            pr["name"], pr["is_git"], pr["agents"], pr["path"]))
    print("[projects] 报告:", p)


if __name__ == "__main__":
    main()

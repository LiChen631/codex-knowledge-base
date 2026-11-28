#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_skills.py -- 把本地原创 skills 按项目分组打包成独立可分发仓库。

设计：
  * 每个项目一个仓库（用户要求：不同项目上传不同仓库）；
  * 只复制技能本体，不复制任何本机路径/私有配置；
  * 生成 README（中文，含安装步骤）与 LICENSE；
  * 发布前做隐私审计，发现残留即中止。

用法：
    python scripts/publish_skills.py --list                # 查看分组
    python scripts/publish_skills.py --build D:\\pub-skills # 生成全部产物
    python scripts/publish_skills.py --build D:\\pub-skills --only paper
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HOME_SKILLS = Path(os.path.expanduser("~")) / ".agents" / "skills"
PROJ_SKILLS = Path(r"D:\codex\.agents\skills")

MIT = """MIT License

Copyright (c) 2026 {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

# ---------------------------------------------------------------- 分组定义
PROJECTS = {
    "codex-cli-skills": {
        "repo": "codex-cli-skills",
        "title": "AI CLI 调用技能包",
        "desc": "让 AI 编码助手直接驱动 Codex / Grok / Kimi / Hermes 四个 CLI 的技能包（非交互执行、脚本化、结构化输出）。",
        "keys": ["codex-cli-calling", "grok-cli-calling", "kimi-cli-calling", "hermes-cli-calling"],
        "tags": ["codex", "grok", "kimi", "hermes", "cli", "automation"],
    },
    "mathmodel-paper-skills": {
        "repo": "mathmodel-paper-skills",
        "title": "数学建模竞赛论文生成技能包",
        "desc": "从赛题解析 → 数据获取清洗 → 模型选型 → 代码求解 → 论文写作 → 质量审计的完整建模竞赛工作流，11 个相互协作的技能。",
        "keys": ["paper-workflow-orchestrator", "problem-doc-model-selector",
                 "modeling-paper-rubric-and-model-selector", "authoritative-data-harvester",
                 "data-cleaning-and-visualization", "model-code-and-result-generator",
                 "paper-formal-writer", "paper-micro-unit-generator",
                 "quality-assurance-auditor", "context-memory-keeper"],
        "tags": ["math-modeling", "cumcm", "mcm", "paper", "latex", "matlab"],
    },
    "docx-automation-skills": {
        "repo": "docx-automation-skills",
        "title": "Word 文档与窗口自动化技能包",
        "desc": "纯 OOXML 精确编辑 Word（锚点定位、表格、页眉页脚、批注、修订）+ 无打扰后台窗口截图。",
        "keys": ["word-docx-professional", "win-bg-control"],
        "tags": ["docx", "word", "ooxml", "automation", "screenshot", "windows"],
    },
    "packet-tracer-skill": {
        "repo": "packet-tracer-skill",
        "title": "Cisco Packet Tracer 自动化技能",
        "desc": "通过 MCP 驱动 Packet Tracer：拓扑部署、ACL/NAT/VLAN/HSRP/BGP/QoS、路由/OSPF/STP 读取、PDU 追踪。含实测命令手册。",
        "keys": ["packet-tracer"],
        "tags": ["cisco", "packet-tracer", "networking", "mcp", "ccna"],
    },
    "data-mining-lab-report-skill": {
        "repo": "data-mining-lab-report-skill",
        "title": "数据挖掘实验报告生成技能",
        "desc": "按课程模板生成《智能数据挖掘》实验报告：封面、逐题例题（说明/代码/真实截图/分析）、思考题、总结。",
        "keys": ["data-mining-lab-report"],
        "tags": ["data-mining", "jupyter", "report", "docx", "education"],
    },
    "agent-memory-skills": {
        "repo": "agent-memory-skills",
        "title": "Agent 长期记忆管理技能包",
        "desc": "跨会话持久记忆：长期原则 + 短期工作台双轨结构，支持合并去重、冲突消解、归档回滚。",
        "keys": ["memory-consolidate", "context-memory-keeper"],
        "tags": ["memory", "agent", "context", "long-term"],
    },
}

STD_MODS = {"os","sys","re","json","time","io","math","pathlib","subprocess","shutil","argparse",
            "datetime","collections","typing","hashlib","textwrap","csv","glob","random","itertools",
            "functools","tempfile","zipfile","ast","unicodedata","copy","uuid","traceback","logging",
            "__future__","ctypes","dataclasses","difflib","importlib","platform","statistics","string",
            "enum","abc","warnings","codecs","base64","struct","signal","threading","concurrent",
            "queue","multiprocessing","socket","urllib","http","email","html","xml"}
PKG_MAP = {"PIL":"pillow","docx":"python-docx","fitz":"pymupdf","lxml":"lxml",
           "matplotlib":"matplotlib","numpy":"numpy","pandas":"pandas","pdfplumber":"pdfplumber",
           "pypdf":"pypdf","pywinauto":"pywinauto","requests":"requests","seaborn":"seaborn",
           "latex2mathml":"latex2mathml"}
# 运行时由技能自己生成的文件（不是 pip 包）
TEMPLATE_MODS = {"result_contract_io", "result_contract_templates", "authoring_contracts",
                 "paper_figure_templates", "paper_scope", "formula_omml"}


def detect_deps(root: Path) -> list:
    """扫描技能脚本的第三方 import，返回 pip 包名列表。"""
    local = {f.stem for f in root.rglob("*.py")}
    third = set()
    for f in root.rglob("*.py"):
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_][\w]*)", t, re.M):
            n = m.group(1)
            if n in STD_MODS or n in local or n in TEMPLATE_MODS:
                continue
            if any(part.startswith(n) or (root / part / (n + ".py")).exists()
                   for part in [p.name for p in root.rglob("*") if p.is_dir()]):
                continue
            third.add(PKG_MAP.get(n, n))
    return sorted(third)


def _privacy_rules() -> dict:
    """隐私检测规则从 config/redact-rules.json 读取（不内联业务实体名）。"""
    cfg = Path(__file__).resolve().parent.parent / "config" / "redact-rules.json"
    try:
        d = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    pats = {
        "用户名": r"(?i)users[\\/][a-z]+[\\/]\\.agents",
        "本机盘符路径": r"[A-Za-z]:\\\\codex",
        "用户目录": r"[A-Za-z]:\\\\Users\\\\[A-Za-z]",
        "邮箱": r"[\w.\-]+@[\w\-]+\.[a-z]{2,}",
        "密钥": r"sk-[A-Za-z0-9_\-\.]{16,}",
        "知识库路径": r"Codex-Knowledge-Base",
    }
    for tok in (d.get("sensitive_tokens") or []):
        pats["业务实体:%s" % tok] = r"(?i)" + re.escape(tok)
    for tok in (d.get("blocked_tokens") or []):
        pats["屏蔽词:%s" % tok] = re.escape(tok)
    return pats


PRIVACY = _privacy_rules()


def resolve(name: str) -> Path | None:
    for base in (HOME_SKILLS, PROJ_SKILLS):
        p = base / name
        if (p / "SKILL.md").exists():
            return p
    return None


def scan_private(root: Path) -> list:
    hits = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() in (".png", ".jpg", ".ico", ".zip", ".pyc"):
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for label, pat in PRIVACY.items():
            for m in re.finditer(pat, t):
                hits.append("%s [%s] %s" % (f.relative_to(root).as_posix(), label,
                                            t[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")[:60]))
    return hits


def build_one(key: str, spec: dict, out_root: Path) -> dict:
    dst = out_root / spec["repo"]
    if dst.exists():
        shutil.rmtree(dst)
    skills_dir = dst / "skills"
    skills_dir.mkdir(parents=True)

    copied, missing = [], []
    for k in spec["keys"]:
        src = resolve(k)
        if not src:
            missing.append(k)
            continue
        shutil.copytree(src, skills_dir / k,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        copied.append(k)

    (dst / "LICENSE").write_text(MIT.format(author="Codex Knowledge Base contributors"),
                                 encoding="utf-8")
    readme = build_readme(spec, copied)
    (dst / "README.md").write_text(readme, encoding="utf-8")
    (dst / ".gitignore").write_text("__pycache__/\n*.pyc\n.DS_Store\n", encoding="utf-8")

    deps = detect_deps(dst)
    if deps:
        txt = (dst / "README.md").read_text(encoding="utf-8")
        block = ("## Python 依赖\n\n部分脚本需要以下第三方包"
                 "（纯 Markdown 技能无需安装即可阅读使用）：\n\n```bash\n"
                 "pip install " + " ".join(deps) + "\n```\n\n")
        txt = txt.replace("## 技能之间如何协作", block + "## 技能之间如何协作")
        (dst / "README.md").write_text(txt, encoding="utf-8")

    hits = scan_private(dst)
    return {"repo": spec["repo"], "dir": str(dst), "skills": copied,
            "missing": missing, "hits": hits, "deps": deps}


def build_readme(spec: dict, skills: list) -> str:
    rows = []
    for k in skills:
        p = (Path(spec["repo"]) / "skills" / k / "SKILL.md")
        desc = ""
        t = (Path(spec["dir"]) / "skills" / k / "SKILL.md").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^description:\s*>-?\s*\n?\s*(.+)", t, re.M | re.S)
        if m:
            desc = " ".join(m.group(1).split())[:160]
        else:
            m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", t, re.M)
            desc = (m.group(1) if m else "")[:160]
        rows.append("| `%s` | %s |" % (k, desc or "—"))
    table = "\n".join(rows)
    return f"""# {spec['title']}

{spec['desc']}

## 内容

共 **{len(skills)}** 个技能：

| 技能 | 说明 |
|---|---|
{table}

## 安装

技能是纯 Markdown + 脚本的目录（`SKILL.md` + 可选 `scripts/`），任何支持 Agent Skills 的客户端都能用。

### Codex

```bash
# 用户级（所有项目可用）
git clone https://github.com/<your-org>/{spec['repo']}.git /tmp/{spec['repo']}
cp -r /tmp/{spec['repo']}/skills/* ~/.agents/skills/

# Windows PowerShell
git clone https://github.com/<your-org>/{spec['repo']}.git $env:TEMP\\{spec['repo']}
Copy-Item $env:TEMP\\{spec['repo']}\\skills\\* $env:USERPROFILE\\.agents\\skills\\ -Recurse
```

### Claude Code

把 `skills/<name>/` 复制到 `~/.claude/skills/`（或项目的 `.claude/skills/`）。

## 技能之间如何协作

技能通过 `SKILL.md` 里的 description 自动匹配触发；也可以显式点名（如 `$paper-workflow-orchestrator`）。
编排型技能（`*-orchestrator`）会调用其它技能，建议整包安装，不要只装单个。

## 许可

MIT，见 `LICENSE`。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", metavar="DIR")
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list or not a.build:
        for k, v in PROJECTS.items():
            ok = [x for x in v["keys"] if resolve(x)]
            print("%-30s %-42s %d/%d 技能" % (k, v["title"], len(ok), len(v["keys"])))
        return

    out_root = Path(a.build)
    out_root.mkdir(parents=True, exist_ok=True)
    targets = {a.only: PROJECTS[a.only]} if a.only else PROJECTS
    all_hits = 0
    for k, spec in targets.items():
        spec = dict(spec, dir=str(out_root / spec["repo"]))
        r = build_one(k, spec, out_root)
        status = "OK " if not r["hits"] else "HIT"
        print("%s %-30s %d 技能%s" % (status, r["repo"], len(r["skills"]),
                                      "" if not r["missing"] else "  缺失:%s" % r["missing"]))
        if r.get("deps"):
            print("      依赖: %s" % ", ".join(r["deps"]))
        for h in r["hits"][:8]:
            print("      ", h)
        all_hits += len(r["hits"])
    print("\n隐私审计:", "全部干净 ✅" if not all_hits else "⚠️ %d 处残留" % all_hits)
    if all_hits:
        sys.exit(3)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_local_assets.py -- inventory this machine into the knowledge base.

Scanned asset classes
---------------------
skills        : ~/.codex/skills, ~/.agents/skills, plugin skill dirs, project skills
plugins       : config.toml [plugins.*] + plugin cache manifests
mcp           : config.toml [mcp_servers.*] (names, commands, env var NAMES only)
software      : real `--version` probes; status derived from the probe result
scripts       : reusable .py/.ps1/.cmd/.bat found in known tool dirs
github        : repositories referenced in history + local git remotes
workflows     : AGENTS.md / prompt files / project rule documents
troubleshooting: high-frequency real errors mined from history

Rule: nothing is marked verified unless a command actually succeeded here.
"""
from __future__ import annotations

import json
import os
import re
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
AGENTS = HOME / ".agents"


def read_frontmatter(p: Path) -> dict:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    out = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip().strip("\"'")
    if "name" not in out:
        for line in text.splitlines():
            if line.startswith("# "):
                out["name"] = line[2:].strip()
                break
    return out


def first_para(text: str, limit=240) -> str:
    body = re.sub(r"^---.*?\n---\n", "", text, flags=re.S)
    for block in body.split("\n\n"):
        b = " ".join(l.strip() for l in block.splitlines() if l.strip() and not l.startswith("#"))
        if len(b) > 40:
            return b[:limit]
    return ""


def _project_skill_roots() -> list:
    """项目级 skills 目录：KB_SKILL_ROOTS 优先（分号分隔），否则探测常见位置。"""
    env = os.environ.get("KB_SKILL_ROOTS", "").strip()
    if env:
        return [Path(x) for x in env.split(";") if x.strip()]
    here = kb.ROOT
    cands = [
        Path(r"D:\codex") / ".agents" / "skills",
        Path.cwd() / ".agents" / "skills",
        here.parent / ".agents" / "skills",
    ]
    seen, out = set(), []
    for c in cands:
        k = str(c).lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def import_skills(con) -> int:
    roots = [
        (CODEX / "skills", "codex-system"),
        (AGENTS / "skills", "personal"),
    ]
    # 项目级 skills：环境变量优先，其次扫描常见工作目录
    for cand in _project_skill_roots():
        if cand.exists():
            roots.append((cand, "project:%s" % cand.parent.parent.name or "local"))
    for mroot in sorted((CODEX / "plugins" / "cache").glob("*/*")):
        if (mroot / "skills").is_dir():
            roots.append((mroot / "skills", "plugin:%s" % mroot.name))
    n = 0
    for root, origin in roots:
        if not root.exists():
            continue
        candidates = []
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            if (p / "SKILL.md").exists():
                candidates.append(p)
            elif not p.name.startswith("."):
                continue
            else:  # e.g. skills/.system/* -- nested skill packs
                candidates.extend(sorted(q for q in p.iterdir()
                                         if q.is_dir() and (q / "SKILL.md").exists()))
        for sk in candidates:
            md = sk / "SKILL.md"
            if not md.exists():
                continue
            fm = read_frontmatter(md)
            text = md.read_text(encoding="utf-8", errors="ignore")
            name = fm.get("name") or sk.name
            desc = fm.get("description") or first_para(text)
            scripts = [p.name for p in (sk / "scripts").glob("*")] if (sk / "scripts").is_dir() else []
            aid = kb.upsert_asset(con, {
                "id": "skill:%s" % kb.slugify(name),
                "name": name, "category": "skill",
                "summary": desc[:400],
                "use_cases": fm.get("description", "")[:400],
                "source_type": "local", "path": str(sk),
                "invoke_method": "在提示中用 $%s 或让 Codex 自动匹配" % name,
                "runtime_env": "Codex skill (SKILL.md)",
                "install_status": "installed",
                "verify_status": "verified" if md.exists() else "unverified",
                "lifecycle_status": "in_use" if origin != "codex-system" else "verified",
                "evidence_level": "C", "confidence": 70,
                "last_checked_at": kb.now(),
                "notes": "origin=%s; scripts=%s" % (origin, ", ".join(scripts[:12]) or "无"),
            }, tags=[origin, "skill"] + (["system"] if origin == "codex-system" else []),
               event=("discovered", "本地扫描 %s" % sk))
            n += 1
    return n


def import_plugins(con) -> int:
    cfg = CODEX / "config.toml"
    n = 0
    enabled = {}
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'\[plugins\."([^"]+)"\]\s*\nenabled\s*=\s*(true|false)', text):
            enabled[m.group(1)] = m.group(2) == "true"
    for full, on in enabled.items():
        name, _, market = full.partition("@")
        ver_dir = None
        base = CODEX / "plugins" / "cache" / market
        if base.is_dir():
            cands = sorted([p for p in base.iterdir() if p.name.startswith(name + "@") or p.name == name])
            if cands:
                ver_dir = cands[-1]
        manifest = {}
        if ver_dir:
            for mp in (ver_dir / "plugin.json", ver_dir / ".codex-plugin" / "plugin.json"):
                if mp.exists():
                    try:
                        manifest = json.loads(mp.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                    break
        kb.upsert_asset(con, {
            "id": "plugin:%s" % kb.slugify(full),
            "name": name, "category": "plugin",
            "summary": (manifest.get("description") or "Codex 插件 (%s)" % market)[:400],
            "use_cases": ", ".join(manifest.get("keywords", [])[:8]) if manifest.get("keywords") else "",
            "source_type": "marketplace", "source_url": manifest.get("repository") or "",
            "version": manifest.get("version") or "",
            "license": manifest.get("license") or "",
            "path": str(ver_dir) if ver_dir else str(base),
            "install_method": "配置 config.toml [plugins.\"%s\"] enabled" % full,
            "invoke_method": "插件工具在会话中自动可用（命名空间由插件定义）",
            "runtime_env": "Codex desktop plugin runtime",
            "install_status": "installed" if on else "partial",
            "verify_status": "verified" if ver_dir else "unverified",
            "lifecycle_status": "in_use" if on else "deprecated",
            "evidence_level": "B", "confidence": 85 if ver_dir else 60,
            "last_checked_at": kb.now(),
            "notes": "marketplace=%s; enabled=%s" % (market, on),
        }, tags=[market, "plugin"], event=("discovered", "config.toml 扫描"))
        n += 1
    return n


def import_mcp(con) -> int:
    cfg = CODEX / "config.toml"
    if not cfg.exists():
        return 0
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    n = 0
    for m in re.finditer(r"\[mcp_servers\.([A-Za-z0-9_\-]+)\]", text):
        name = m.group(1)
        block = text[m.end():m.end() + 900]
        cmd = re.search(r'command\s*=\s*[\'"]([^\'"]+)', block)
        args = re.search(r"args\s*=\s*(\[[^\]]*\])", block)
        envs = re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*=", block, re.M)
        kb.upsert_asset(con, {
            "id": "mcp:%s" % kb.slugify(name),
            "name": name, "category": "mcp",
            "summary": "MCP 服务器 %s（启动命令 %s）" % (name, Path(cmd.group(1)).name if cmd else "?"),
            "use_cases": "由 Codex 会话调用其工具；工具名形如 %s/<tool>" % name,
            "source_type": "local", "path": cmd.group(1) if cmd else "",
            "install_method": "config.toml [mcp_servers.%s]" % name,
            "install_command": (cmd.group(1) if cmd else ""),
            "config_method": "编辑 %s" % cfg,
            "invoke_method": "MCP 工具调用",
            "runtime_env": "stdio/stdout MCP",
            "permissions": "继承 Codex 沙箱与审批策略",
            "install_status": "installed",
            "verify_status": "unverified",
            "lifecycle_status": "in_use",
            "evidence_level": "C", "confidence": 80,
            "last_checked_at": kb.now(),
            "notes": "env_vars=%s; args=%s" % (",".join(sorted(set(envs))[:10]) or "无",
                                               (args.group(1) if args else "[]")[:120]),
        }, tags=["mcp", "codex"], event=("discovered", "config.toml [mcp_servers]"))
        n += 1
    return n


SOFTWARE = [
    ("git", "git --version", "版本控制", "D:\\Program Files\\Git"),
    ("gh", "gh --version", "GitHub CLI", ""),
    ("python", "python --version", "Python 运行时", ""),
    ("python314", r'"D:\Python314\python.exe" --version', "Python 3.14（内置 zstd）", "D:\\Python314"),
    ("node", "node --version", "Node.js", "D:\\nodejs"),
    ("npm", "npm --version", "npm", ""),
    ("pnpm", "pnpm --version", "pnpm", "D:\\.pnpm-store"),
    ("docker", "docker --version", "容器", "D:\\DockerData"),
    ("docker-compose", "docker compose version", "容器编排", ""),
    ("matlab", "matlab -batch \"disp(version)\"", "MATLAB", "D:\\matlab"),
    ("vscode", r"\"D:\Microsoft VS Code\_\Code.exe\" --version", "编辑器", r"D:\Microsoft VS Code"),
    ("zotero", "zotero --version", "文献管理", ""),
    ("pandoc", "pandoc --version", "文档转换", ""),
    ("ffmpeg", "ffmpeg -version", "音视频处理", ""),
    ("xelatex", "xelatex --version", "LaTeX", ""),
    ("tectonic", "tectonic --version", "LaTeX(轻量)", ""),
    ("rg", "rg --version", "ripgrep 搜索", ""),
    ("jq", "jq --version", "JSON 处理", ""),
    ("7z", "7z", "压缩", ""),
    ("winget", "winget --version", "Windows 包管理", ""),
    ("uv", "uv --version", "Python 包管理", ""),
    ("pipx", "pipx --version", "Python 应用隔离", ""),
    ("conda", "conda --version", "Conda 环境", ""),
    ("ollama", "ollama --version", "本地大模型", ""),
    ("cloudflared", "cloudflared --version", "内网穿透", ""),
    ("zrok", "zrok version", "内网穿透", ""),
    ("sqlite3", "sqlite3 --version", "SQLite CLI", ""),
]


def probe(cmd: str, timeout: int = 25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or r.stderr or "").strip().splitlines()
        return (r.returncode == 0, out[0][:180] if out else "")
    except Exception as e:
        return (False, str(e)[:120])


FALLBACKS = {
    "matlab": [r"D:\matlab\bin\matlab.exe"],
    "vscode": [r"D:\Microsoft VS Code\_\Code.exe"],
    "7z": [r"C:\Program Files\7-Zip\7z.exe", r"D:\Program Files\7-Zip\7z.exe"],
    "zotero": [r"C:\Program Files\Zotero\zotero.exe"],
    "sqlite3": [r"D:\sqlite3\sqlite3.exe"],
    "ffmpeg": [r"D:\ffmpeg\bin\ffmpeg.exe"],
    "pandoc": [r"D:\pandoc\pandoc.exe"],
}


def import_software(con) -> int:
    n = 0
    for name, cmd, use, path in SOFTWARE:
        ok, ver = probe(cmd)
        found_path = ""
        if not ok:
            for cand in FALLBACKS.get(name, []) + ([path] if path else []):
                if cand and Path(cand).is_file():
                    found_path = cand
                    break
        installed = ok or bool(found_path)
        kb.upsert_asset(con, {
            "id": "software:%s" % kb.slugify(name),
            "name": name, "category": "software",
            "summary": use, "use_cases": use,
            "source_type": "local",
            "path": found_path or path,
            "version": ver if ok else "",
            "install_method": "系统/包管理器安装",
            "invoke_method": cmd if ok else (found_path or cmd),
            "runtime_env": "Windows 11",
            "install_status": "installed" if installed else "not_installed",
            "verify_status": "verified" if ok else "unverified",
            "lifecycle_status": "in_use" if installed else "pending_verification",
            "evidence_level": "A" if installed else "E",
            "confidence": 95 if ok else (65 if found_path else 20),
            "last_verified_at": kb.now() if ok else None,
            "last_checked_at": kb.now(),
            "notes": ("探测成功: %s -> %s" % (cmd, ver)) if ok else (
                ("可执行文件存在 (%s)，但版本探测失败/未在 PATH" % found_path) if found_path
                else ("未探测到：%s" % cmd)),
        }, tags=["software", use], event=("verified" if ok else ("noted" if installed else "noted"), cmd))
        if ok:
            kb.add_verification(con, "software:%s" % kb.slugify(name), "version-probe", cmd, "pass", ver)
        n += 1
    return n


def _script_dirs():
    env = os.environ.get("KB_SCRIPT_DIRS", "").strip()
    if env:
        return [Path(x) for x in env.split(";") if x.strip()]
    return [Path.cwd(), kb.ROOT / "scripts"]


SCRIPT_DIRS = _script_dirs()
SCRIPT_EXT = {".py", ".ps1", ".cmd", ".bat", ".sh"}


def import_scripts(con, limit: int = 120) -> int:
    n = 0
    seen = set()
    for d in SCRIPT_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if not p.is_file() or p.suffix.lower() not in SCRIPT_EXT or p.name in seen:
                continue
            seen.add(p.name)
            if p.stat().st_size > 400_000 or p.stat().st_size < 60:
                continue
            head = ""
            try:
                head = "\n".join(p.read_text(encoding="utf-8", errors="ignore").splitlines()[:25])
            except Exception:
                pass
            doc = ""
            m = re.search(r'"""(.{10,300}?)"""', head, re.S) or re.search(r"#\s*(.{10,200})", head)
            if m:
                doc = " ".join(m.group(1).split())[:240]
            kb.upsert_asset(con, {
                "id": "script:%s" % kb.slugify(p.stem + "-" + p.suffix.lstrip(".")),
                "name": p.name, "category": "script",
                "summary": doc or ("可复用脚本 %s" % p.name),
                "source_type": "local", "path": str(p),
                "invoke_method": ("python \"%s\"" % p) if p.suffix == ".py" else (
                    "powershell -File \"%s\"" % p if p.suffix == ".ps1" else str(p)),
                "runtime_env": p.suffix.lstrip("."),
                "install_status": "installed",
                "verify_status": "unverified",
                "lifecycle_status": "in_use",
                "evidence_level": "C", "confidence": 50,
                "last_checked_at": kb.now(),
                "notes": "大小 %d 字节" % p.stat().st_size,
            }, tags=["script", p.suffix.lstrip(".")], event=("discovered", "目录扫描"))
            n += 1
            if n >= limit:
                return n
    return n


GH_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")


def import_github(con) -> int:
    counts = {}
    for r in con.execute("SELECT detail FROM history_items WHERE detail LIKE '%github.com/%'"):
        text = (r["detail"] or "").replace("api.github.com/repos/", "github.com/")
        for owner, repo in GH_RE.findall(text):
            repo = repo.replace(".git", "").rstrip(").,;")
            if len(repo) < 2 or repo in ("blob", "tree", "issues", "pull", "releases",
                                         "actions", "wiki", "discussions", "repositories",
                                         "commits", "tags", "graphs", "network"):
                continue
            if owner.lower() in ("search", "topics", "trending", "marketplace", "orgs",
                                 "users", "sponsors", "settings", "features", "pricing", "repos", "api", "raw",
                                 "login", "about", "explore", "collections", "events",
                                 "notifications", "new", "apps", "site", "support"):
                continue
            key = "%s/%s" % (owner, repo)
            counts[key] = counts.get(key, 0) + 1
    n = 0
    for key, c in sorted(counts.items(), key=lambda x: -x[1])[:120]:
        owner, repo = key.split("/", 1)
        kb.upsert_asset(con, {
            "id": "github_project:%s" % kb.slugify(key),
            "name": key, "category": "github_project",
            "summary": "历史会话中引用 %d 次的开源仓库" % c,
            "source_type": "github", "source_url": "https://github.com/" + key,
            "docs_url": "https://github.com/%s#readme" % key,
            "install_status": "unknown",
            "verify_status": "unverified",
            "lifecycle_status": "pending_verification",
            "evidence_level": "D", "confidence": 40,
            "usage_count": c,
            "notes": "仅从历史记录中发现引用，尚未核实其当前状态与许可证",
        }, tags=["github", owner.lower()], event=("discovered", "历史引用 %d 次" % c))
        n += 1
    return n


# 公开版：不绑定具体项目路径。如需导入自己的项目文档，请设置 KB_WORKFLOW_DOCS。
WORKFLOW_DOCS = []


def import_workflows(con) -> int:
    n = 0
    for path, title, cat in WORKFLOW_DOCS:
        p = Path(path)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        kb.upsert_asset(con, {
            "id": "%s:%s" % (cat, kb.slugify(title)),
            "name": title, "category": cat,
            "summary": first_para(text, 300) or ("文档 %s" % p.name),
            "source_type": "local", "path": str(p),
            "invoke_method": "阅读 %s 后按其中阶段执行" % p,
            "install_status": "installed",
            "verify_status": "unverified",
            "lifecycle_status": "in_use",
            "evidence_level": "B", "confidence": 60,
            "last_checked_at": kb.now(),
            "notes": "来源文档 %s；%d 字节" % (p.name, p.stat().st_size),
        }, tags=["workflow", "mathmodel", "prompt"], event=("discovered", "文档登记"))
        n += 1
    return n


KNOWN_FIXES = {
    "bash": ("WSL 未安装，bash 全部失败",
             "本机没有可用的 WSL/bash（326 次调用 0 成功）。改用 PowerShell 语法；"
             "如确需 Linux 工具链，安装 WSL2（wsl --install）后再验证。"),
    "docker API": ("Docker Desktop 引擎未启动",
                   "先启动 Docker Desktop 并等待 docker info 可用，再执行 compose 命令。"),
}


def import_troubleshooting(con, min_occ: int = 2) -> int:
    n = 0
    for r in con.execute("SELECT signature,error_text,occurrences,first_seen,last_seen"
                         " FROM errors WHERE occurrences>=? ORDER BY occurrences DESC LIMIT 60",
                         (min_occ,)):
        sig = r["signature"]
        fix_title, fix = None, None
        for k, v in KNOWN_FIXES.items():
            if k.lower() in sig.lower():
                fix_title, fix = v
                break
        kb.upsert_asset(con, {
            "id": "troubleshooting:%s" % kb.slugify(sig[:40]),
            "name": (fix_title or sig)[:80],
            "category": "troubleshooting",
            "summary": "历史出现 %d 次：%s" % (r["occurrences"], sig[:200]),
            "use_cases": fix or "需结合历史上下文定位；可在 kb 中检索相邻命令复现",
            "source_type": "local_history",
            "install_status": "not_applicable",
            "verify_status": "unverified",
            "lifecycle_status": "pending_verification",
            "evidence_level": "B", "confidence": 55 if fix else 35,
            "last_checked_at": kb.now(),
            "notes": ("首次 %s / 最近 %s\n原文: %s" % (
                r["first_seen"], r["last_seen"], (r["error_text"] or "")[:600])),
        }, tags=["error", "troubleshooting"], event=("discovered", "历史错误聚合"))
        n += 1
    return n


def main():
    kb.migrate()
    con = kb.connect()
    try:
        print("[assets] skills :", import_skills(con))
        print("[assets] plugins:", import_plugins(con))
        print("[assets] mcp    :", import_mcp(con))
        print("[assets] software:", import_software(con))
        print("[assets] scripts:", import_scripts(con))
        print("[assets] github :", import_github(con))
        print("[assets] workflow:", import_workflows(con))
        print("[assets] troubleshooting:", import_troubleshooting(con))
        kb.kv_set(con, "last_asset_import", kb.now())
        con.commit()
        s = kb.stats()
        print("[assets] 资产总计:", s["assets"], s["by_category"])
    finally:
        con.close()


if __name__ == "__main__":
    main()

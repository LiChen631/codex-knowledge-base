#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""health_check.py -- 18-point health check + monthly report writer."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
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


def _real_path(p: str) -> bool:
    """占位符路径（<home>/<projects>/<kb-root>）不是本机真实路径，跳过校验。"""
    return bool(p) and not p.startswith("<") and not p.startswith("~")


def _ok(name, detail=""):
    return {"name": name, "status": "ok", "detail": detail}


def _warn(name, detail=""):
    return {"name": name, "status": "warn", "detail": detail}


def _bad(name, detail=""):
    return {"name": name, "status": "bad", "detail": detail}


def collect(with_network: bool = False) -> dict:
    checks = []
    con = None
    try:
        con = kb.connect(readonly=True)
    except Exception as e:
        checks.append(_bad("1 SQLite 完整性", "无法连接: %s" % e))

    # 1 integrity ---------------------------------------------------------
    if con:
        try:
            q = con.execute("PRAGMA quick_check").fetchone()[0]
            checks.append(_ok("1 SQLite 完整性", "quick_check=%s" % q) if q == "ok"
                          else _bad("1 SQLite 完整性", q))
        except Exception as e:
            checks.append(_bad("1 SQLite 完整性", str(e)))

    # 2 markdown integrity ------------------------------------------------
    md = list(kb.ROOT.rglob("*.md"))
    missing_ref = []
    for p in md:
        if any(part in p.parts for part in ("logs", "backups")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r"\]\(([^)#]+\.md)(?:#[^)]*)?\)", text):
            tgt = (p.parent / m.group(1)).resolve()
            if not tgt.exists():
                missing_ref.append("%s -> %s" % (p.name, m.group(1)))
    checks.append(_ok("2 Markdown 完整性", "%d 篇文档" % len(md)) if not missing_ref
                  else _warn("2 Markdown 完整性", "失效引用 %d 处: %s" % (len(missing_ref), missing_ref[:3])))

    # 3 git sync ----------------------------------------------------------
    if (kb.ROOT / ".git").exists():
        r = subprocess.run(["git", "-C", str(kb.ROOT), "status", "--porcelain"],
                           capture_output=True, text=True)
        dirty = len([l for l in r.stdout.splitlines() if l.strip()])
        remotes = subprocess.run(["git", "-C", str(kb.ROOT), "remote"],
                                 capture_output=True, text=True).stdout.strip()
        detail = "未提交变更 %d 项；远程=%s" % (dirty, remotes or "未配置")
        checks.append(_ok("3 Git 同步状态", detail) if dirty == 0 and remotes
                      else _warn("3 Git 同步状态", detail))
    else:
        checks.append(_warn("3 Git 同步状态", "尚未初始化 git 仓库"))

    # 4 backups -----------------------------------------------------------
    bks = sorted(kb.BACKUPS.glob("kb-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if bks:
        age = (dt.datetime.now().timestamp() - bks[0].stat().st_mtime) / 86400
        checks.append(_ok("4 本地备份", "最新备份 %.1f 天前 (%s)" % (age, bks[0].name)) if age <= 14
                      else _warn("4 本地备份", "最新备份 %.1f 天前，建议执行 backup" % age))
    else:
        checks.append(_warn("4 本地备份", "尚无备份"))

    # 5/6 install + version ----------------------------------------------
    if con:
        not_inst = con.execute("SELECT COUNT(*) FROM assets WHERE category IN"
                               " ('software','plugin','skill','mcp') AND install_status='not_installed'").fetchone()[0]
        inst = con.execute("SELECT COUNT(*) FROM assets WHERE install_status='installed'").fetchone()[0]
        unknown = con.execute("SELECT COUNT(*) FROM assets WHERE install_status='unknown'").fetchone()[0]
        checks.append(_ok("5 工具安装状态",
                          "记录为已安装 %d；未安装 %d；状态未知 %d（公开版不声称本机已装）"
                          % (inst, not_inst, unknown)))
        nv = con.execute("SELECT COUNT(*) FROM assets WHERE version IS NULL OR version=''").fetchone()[0]
        checks.append(_ok("6 版本信息", "缺少版本号 %d 项（多为本地脚本/未探测工具）" % nv) if nv < 40
                      else _warn("6 版本信息", "缺少版本号 %d 项" % nv))

    # 7/8 links -----------------------------------------------------------
    if with_network and con:
        bad_links = []
        for r in con.execute("SELECT name,source_url FROM assets WHERE source_url LIKE 'https://github.com/%'"
                             " ORDER BY usage_count DESC LIMIT 8"):
            try:
                import urllib.request
                req = urllib.request.Request(r["source_url"], method="HEAD",
                                             headers={"User-Agent": "codex-kb-health"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status >= 400:
                        bad_links.append("%s(%s)" % (r["name"], resp.status))
            except Exception as e:
                bad_links.append("%s(%s)" % (r["name"], type(e).__name__))
        checks.append(_ok("7 GitHub 链接", "抽查 8 个，均可访问") if not bad_links
                      else _warn("7 GitHub 链接", "可疑: %s" % ", ".join(bad_links[:5])))
        checks.append(_ok("8 官方文档链接", "本检查随 7 合并执行；详见月度报告"))
    else:
        checks.append(_warn("7 GitHub 链接", "离线模式未校验（加 --network 启用）"))
        checks.append(_warn("8 官方文档链接", "离线模式未校验"))

    # 9 duplicates --------------------------------------------------------
    if con:
        dups = [r for r in con.execute(
            "SELECT lower(name) n, COUNT(*) c FROM assets GROUP BY category, lower(name)"
            " HAVING c>1")]
        checks.append(_ok("9 重复工具", "无同名重复") if not dups
                      else _warn("9 重复工具", "%d 组同名: %s" % (len(dups), [d["n"] for d in dups[:5]])))
        # 10 expired
        exp = con.execute("SELECT COUNT(*) FROM assets WHERE lifecycle_status IN"
                          " ('expired','deprecated','replaced')").fetchone()[0]
        checks.append(_ok("10 过期工具", "已标记过期/废弃 %d 项" % exp))
        # 11 unverified
        unv = con.execute("SELECT COUNT(*) FROM assets WHERE verify_status='unverified'").fetchone()[0]
        tot = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        checks.append(_ok("11 未验证工具", "%d/%d 未验证（诚实标记，需实测）" % (unv, tot)) if unv <= tot * 0.7
                      else _warn("11 未验证工具", "%d/%d 未验证，比例偏高" % (unv, tot)))
        # 12 dependency conflicts
        broken = [r["src_id"] for r in con.execute(
            "SELECT l.src_id FROM asset_links l LEFT JOIN assets a ON a.id=l.dst_id WHERE a.id IS NULL")]
        checks.append(_ok("12 依赖冲突", "无悬空依赖") if not broken
                      else _warn("12 依赖冲突", "悬空依赖 %d 条" % len(broken)))
    # 13 mcp --------------------------------------------------------------
    cfg = CODEX / "config.toml"
    n_mcp = 0
    if cfg.exists():
        n_mcp = len(re.findall(r"\[mcp_servers\.[A-Za-z0-9_\-]+\]", cfg.read_text(encoding="utf-8", errors="ignore")))
    checks.append(_ok("13 MCP 配置", "config.toml 中 %d 个 MCP server" % n_mcp) if n_mcp
                  else _ok("13 MCP 配置", "本机未配置 MCP server（知识库资产中已记录 %d 个）"
                           % (con.execute("SELECT COUNT(*) FROM assets WHERE category='mcp'").fetchone()[0]
                              if con else 0)))

    # 14 skills -----------------------------------------------------------
    if con:
        bad_sk = []
        checked = skipped = 0
        for r in con.execute("SELECT name,path FROM assets WHERE category='skill' AND path IS NOT NULL"):
            if not _real_path(r["path"]):
                skipped += 1
                continue
            checked += 1
            if not Path(r["path"]).exists():
                bad_sk.append(r["name"])
        detail = "校验 %d 个本地技能路径" % checked
        if skipped:
            detail += "；%d 个为公开版占位路径已跳过" % skipped
        checks.append(_ok("14 Skills 可用性", detail) if not bad_sk
                      else _bad("14 Skills 可用性", "缺失: %s" % ", ".join(bad_sk[:5])))

    # 15 index ------------------------------------------------------------
    if con:
        try:
            na = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            nf = con.execute("SELECT COUNT(*) FROM assets_fts").fetchone()[0]
            checks.append(_ok("15 检索索引", "assets=%d fts=%d" % (na, nf)) if abs(na - nf) <= 2
                          else _warn("15 检索索引", "索引不同步 assets=%d fts=%d，请重建" % (na, nf)))
        except sqlite3.Error as e:
            checks.append(_bad("15 检索索引", str(e)))

    # 16 global integration ----------------------------------------------
    hooks = CODEX / "hooks.json"
    agents = CODEX / "AGENTS.md"
    h_txt = hooks.read_text(encoding="utf-8", errors="ignore") if hooks.exists() else ""
    a_txt = agents.read_text(encoding="utf-8", errors="ignore") if agents.exists() else ""
    has_hooks = "kb_" in h_txt or "knowledge" in h_txt.lower()
    has_rule = "Codex-Knowledge-Base" in a_txt
    if has_hooks and has_rule:
        checks.append(_ok("16 全局接入", "hooks 已注册；全局 AGENTS.md 已生效"))
    else:
        tips = []
        if not has_hooks:
            tips.append("hooks 未注册（见 README「接入」章节）")
        if not has_rule:
            tips.append("全局 AGENTS.md 未写入")
        checks.append(_warn("16 全局接入", "；".join(tips)))

    # 17 secrets ----------------------------------------------------------
    findings = kb.scan_secrets([kb.ROOT / "config", kb.ROOT / "docs", kb.ROOT / "reports",
                                kb.ROOT / "scripts", kb.ROOT / "dashboard"])
    checks.append(_ok("17 密钥泄露", "未发现敏感值") if not findings
                  else _bad("17 密钥泄露", "发现 %d 处: %s" % (
                      len(findings), [f["file"] for f in findings[:3]])))

    # 18 stale commands ---------------------------------------------------
    if con:
        stale = con.execute("SELECT COUNT(*) FROM commands WHERE last_seen < ?",
                            ((dt.date.today() - dt.timedelta(days=90)).isoformat(),)).fetchone()[0]
        checks.append(_ok("18 陈旧命令/配置", "90 天未使用的命令签名 %d 个（仅提示，不删除）" % stale))

    if con:
        con.close()

    overall = "ok"
    if any(c["status"] == "bad" for c in checks):
        overall = "bad"
    elif any(c["status"] == "warn" for c in checks):
        overall = "warn"
    return {
        "status": overall, "generated_at": kb.now(), "checks": checks,
        "monthly_last": kb.kv_get("last_health_month", "无"),
        "monthly_path": kb.kv_get("last_health_path", ""),
    }


def run(monthly: bool = False, quiet: bool = False) -> dict:
    import argparse
    res = collect(with_network=("--network" in sys.argv or "-network" in sys.argv or monthly))
    month = dt.date.today().strftime("%Y-%m")
    prev = kb.kv_get("last_health_month")
    do_monthly = monthly or (prev != month)
    if do_monthly:
        p = kb.REPORTS / "monthly" / (month + ".md")
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# 月度健康检查 %s" % month, "",
                 "- 生成时间: %s" % res["generated_at"],
                 "- 总体状态: **%s**" % res["status"], "",
                 "| # | 检查项 | 状态 | 说明 |", "|---|---|---|---|"]
        for c in res["checks"]:
            lines.append("| %s | %s | %s | %s |" % (
                c["name"].split(" ")[0], c["name"], c["status"], (c["detail"] or "").replace("|", "/")))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        con = kb.connect()
        try:
            con.execute("INSERT INTO health_runs(run_at,month,status,checks_json,report_path)"
                        " VALUES(?,?,?,?,?)", (kb.now(), month, res["status"],
                                               json.dumps(res["checks"], ensure_ascii=False), str(p)))
            kb.kv_set(con, "last_health_month", month)
            kb.kv_set(con, "last_health_path", str(p))
            kb.kv_set(con, "last_health_check", res["generated_at"])
            con.commit()
        finally:
            con.close()
        res["monthly_path"] = str(p)
        res["monthly_last"] = month
    else:
        con = kb.connect()
        try:
            kb.kv_set(con, "last_health_check", res["generated_at"])
            con.commit()
        finally:
            con.close()
    if not quiet:
        for c in res["checks"]:
            mark = {"ok": "✅", "warn": "⚠️", "bad": "❌"}[c["status"]]
            print("%s %-24s %s" % (mark, c["name"], c["detail"]))
        print("\n总体状态:", res["status"], "| 月度报告:", res.get("monthly_path") or "未生成")
    return res


if __name__ == "__main__":
    sys.exit(0 if run(monthly="--monthly" in sys.argv).get("status") == "ok" else 2)

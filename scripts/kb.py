#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb.py -- command line entry point for the Codex Global Knowledge Base.

Examples
--------
    python kb.py init
    python kb.py import-history
    python kb.py import-assets
    python kb.py search "数模 论文"
    python kb.py list --category skill --installed
    python kb.py show skill:openai-docs
    python kb.py verify skill:openai-docs --cmd "codex --version"
    python kb.py health --monthly
    python kb.py backup --tag daily
    python kb.py sync --message "kb: update"
    python kb.py serve --port 8787
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbcore as kb  # noqa: E402

# Force UTF-8 console output so Chinese text survives Windows pipes.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass



def _table(rows, cols):
    if not rows:
        print("(无记录)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, "") or "")) for r in rows)) for c in cols}
    widths = {c: min(w, 46) for c, w in widths.items()}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "") or "")[:widths[c]].ljust(widths[c]) for c in cols))


def cmd_init(args):
    applied = kb.migrate(verbose=True)
    print("[init] 迁移完成:", applied or "无待应用迁移")
    con = kb.connect()
    try:
        kb.kv_set(con, "kb_version", "1.0.0")
        kb.kv_set(con, "initialized_at", kb.kv_get("initialized_at", kb.now()))
        kb.kv_set(con, "root", str(kb.ROOT))
        con.commit()
    finally:
        con.close()
    seed = kb.ROOT / "database" / "seed.json"
    if seed.exists():
        con = kb.connect()
        try:
            empty = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        finally:
            con.close()
        if empty:
            import export_seed
            print("[init] 数据库为空，从 seed.json 恢复:", export_seed.load(seed), "项资产")
    n = kb.index_docs()
    print("[init] 文档索引:", n, "篇")
    print("[init] 数据库:", kb.DB_PATH)
    print("[init] 知识库根目录:", kb.ROOT)


def cmd_add(args):
    data = {}
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    data.update({k: v for k, v in {
        "name": args.name, "category": args.category, "summary": args.summary,
        "use_cases": args.use_cases, "source_url": args.url, "install_command": args.install,
        "invoke_method": args.invoke, "path": args.path, "notes": args.notes,
        "install_status": args.install_status, "evidence_level": args.evidence,
        "lifecycle_status": args.lifecycle,
    }.items() if v})
    if not data.get("name"):
        sys.exit("--name 必填")
    con = kb.connect()
    try:
        aid = kb.upsert_asset(con, data,
                              tags=(args.tags or "").split(",") if args.tags else None,
                              event=("discovered", "CLI 添加"))
    finally:
        con.close()
    print("已保存:", aid)


def cmd_list(args):
    con = kb.connect(readonly=True)
    try:
        sql = "SELECT id,name,category,install_status,verify_status,lifecycle_status,"\
              "evidence_level,summary FROM assets WHERE 1=1"
        a = []
        if args.category:
            sql += " AND category=?"; a.append(args.category)
        if args.installed:
            sql += " AND install_status='installed'"
        if args.verified:
            sql += " AND verify_status='verified'"
        if args.raw:
            sql += " AND verify_status='unverified'"
        if args.tag:
            sql += " AND id IN (SELECT asset_id FROM asset_tags WHERE tag=?)"; a.append(args.tag)
        sql += " ORDER BY category, name LIMIT ?"; a.append(args.limit)
        rows = [dict(r) for r in con.execute(sql, a)]
    finally:
        con.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        _table(rows, ["name", "category", "install_status", "verify_status",
                      "lifecycle_status", "evidence_level", "summary"])


def cmd_search(args):
    rows = kb.search(args.query, category=args.category, limit=args.limit,
                     include_history=args.history)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        print("%-34s [%s] score=%-6s %s: %s" % (
            r["name"][:34], r["category"], r["score"], r["lifecycle"],
            (r["summary"] or "")[:70]))
        if r.get("why"):
            print("    · " + " / ".join(r["why"]))
    if not rows:
        print("未命中。建议：python scripts/kb.py search \"关键词\" --history")


def cmd_show(args):
    a = kb.get_asset(args.id)
    if not a:
        sys.exit("未找到资产: " + args.id)
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
        return
    for k, v in a.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print("\n[%s]" % k)
            for item in v:
                print("  -", json.dumps(item, ensure_ascii=False))
        else:
            print("%-18s %s" % (k, v))


def cmd_verify(args):
    a = kb.get_asset(args.id)
    if not a:
        sys.exit("未找到资产: " + args.id)
    cmd = args.cmd or a.get("install_command")
    if not cmd:
        sys.exit("需要 --cmd 指定验证命令")
    print("[verify]", args.id, "->", cmd)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=args.timeout,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print((r.stdout or "")[:800])
    if r.stderr:
        print("STDERR:", (r.stderr or "")[:400])
    con = kb.connect()
    try:
        kb.add_verification(con, args.id, "cli", cmd, "pass" if ok else "fail",
                            (r.stdout or "") + (r.stderr or ""))
        kb.add_event(con, args.id, "verified" if ok else "failed", cmd)
    finally:
        con.close()
    print("结果:", "PASS ✅" if ok else "FAIL ❌")
    sys.exit(0 if ok else 1)


def cmd_deps(args):
    a = kb.get_asset(args.id)
    if not a:
        sys.exit("未找到资产: " + args.id)
    down = kb.dependency_chain(args.id, "down", args.depth)
    up = kb.dependency_chain(args.id, "up", args.depth)
    if args.json:
        print(json.dumps({"asset": args.id, "depends_on": down, "required_by": up},
                         ensure_ascii=False, indent=2))
        return
    print("资产:", a["name"], "(%s)" % a["category"])
    print("\n[向下] 它依赖 / 使用的对象：")
    if not down:
        print("  （无记录）")
    for d in down:
        print("  %s%s [%s] %s/%s  <- %s" % ("  " * (d["lvl"] - 1), d["name"] or d["id"],
                                            d["category"] or "?", d["install_status"],
                                            d["verify_status"], d["relation"]))
    print("\n[向上] 依赖它的对象（改动影响面）：")
    if not up:
        print("  （无记录）")
    for d in up:
        print("  %s%s [%s] --%s" % ("  " * (d["lvl"] - 1), d["name"] or d["id"],
                                    d["category"] or "?", d["relation"]))


def cmd_audit(args):
    import audit
    audit.main()


def cmd_health(args):
    import health_check
    res = health_check.run(monthly=args.monthly, quiet=False)
    sys.exit(0 if res.get("status") == "ok" else 2)


def cmd_backup(args):
    p = kb.backup(args.tag or "manual")
    print("备份完成:", p, "(%.1f MB)" % (p.stat().st_size / 1024 / 1024))


def cmd_restore(args):
    p = kb.restore(args.zip, force=args.force)
    print("恢复完成:", p)


def cmd_sync(args):
    """git add/commit (+push if remote exists). Refuses on detected secrets."""
    root = kb.ROOT
    if not (root / ".git").exists():
        sys.exit("尚未初始化 git 仓库，请先运行 scripts/sync.ps1 -Init")
    findings = kb.scan_secrets([root / "config", root / "docs", root / "reports",
                                root / "scripts", root / "dashboard", root / "README.md",
                                root / "AGENTS.md"])
    if findings:
        print("⛔ 检测到疑似密钥，已中止同步：")
        for f in findings[:20]:
            print("  %s:%s  %s  %s" % (f["file"], f["line"], f["type"], f["preview"]))
        sys.exit(3)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    msg = args.message or "kb: sync %s" % kb.now()
    r = subprocess.run(["git", "-C", str(root), "commit", "-m", msg],
                       capture_output=True, text=True)
    print((r.stdout or r.stderr).strip()[:600])
    remotes = subprocess.run(["git", "-C", str(root), "remote"], capture_output=True, text=True).stdout.split()
    if remotes and args.push:
        p = subprocess.run(["git", "-C", str(root), "push"], capture_output=True, text=True)
        print((p.stdout + p.stderr).strip()[:800])
    con = kb.connect()
    try:
        con.execute("INSERT INTO sync_log(action,status,detail,happened_at) VALUES(?,?,?,?)",
                    ("commit", "ok", msg, kb.now()))
        con.commit()
    finally:
        con.close()


def cmd_context(args):
    print(kb.build_context(args.prompt or "", limit=args.limit))


def cmd_serve(args):
    import serve
    serve.main(host=args.host, port=args.port, open_browser=args.open)


def cmd_doctor(args):
    info = {
        "root": str(kb.ROOT), "db": str(kb.DB_PATH),
        "db_exists": kb.DB_PATH.exists(),
        "db_size_mb": round(kb.DB_PATH.stat().st_size / 1024 / 1024, 2) if kb.DB_PATH.exists() else 0,
        "python": sys.version.split()[0],
        "integrity": kb.integrity(),
        "git": (kb.ROOT / ".git").exists(),
    }
    try:
        info["stats"] = kb.stats()
    except Exception as e:
        info["stats"] = "error: %s" % e
    print(json.dumps(info, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kb", description="Codex 全局知识库 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化数据库与文档索引").set_defaults(func=cmd_init)
    sub.add_parser("migrate", help="应用数据库迁移").set_defaults(
        func=lambda a: print(kb.migrate(verbose=True)))

    p = sub.add_parser("add", help="添加/更新资产")
    p.add_argument("--name", required=False)
    p.add_argument("--category", default="other", choices=kb.CATEGORIES)
    p.add_argument("--summary"); p.add_argument("--use-cases", dest="use_cases")
    p.add_argument("--url"); p.add_argument("--install"); p.add_argument("--invoke")
    p.add_argument("--path"); p.add_argument("--notes"); p.add_argument("--tags")
    p.add_argument("--json"); p.add_argument("--evidence", default="C", choices=kb.EVIDENCE)
    p.add_argument("--install-status", dest="install_status", default="installed")
    p.add_argument("--lifecycle", default="pending_verification")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="列出资产")
    p.add_argument("--category", choices=kb.CATEGORIES)
    p.add_argument("--tag"); p.add_argument("--installed", action="store_true")
    p.add_argument("--verified", action="store_true"); p.add_argument("--raw", action="store_true")
    p.add_argument("--limit", type=int, default=200); p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="混合检索")
    p.add_argument("query"); p.add_argument("--category", choices=kb.CATEGORIES)
    p.add_argument("--limit", type=int, default=15); p.add_argument("--history", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="查看资产详情")
    p.add_argument("id"); p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("verify", help="执行验证命令并记录结果")
    p.add_argument("id"); p.add_argument("--cmd"); p.add_argument("--timeout", type=int, default=120)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("deps", help="查看依赖 / 影响链")
    p.add_argument("id"); p.add_argument("--depth", type=int, default=6)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_deps)

    p = sub.add_parser("scan-projects", help="扫描已有项目接入状况")
    p.set_defaults(func=lambda a: __import__("scan_projects").main())

    p = sub.add_parser("audit", help="版本与维护状态审计")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("health", help="健康检查")
    p.add_argument("--monthly", action="store_true")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("backup", help="备份知识库")
    p.add_argument("--tag", default="manual")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="从备份恢复")
    p.add_argument("zip"); p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("sync", help="提交并（可选）推送 Git")
    p.add_argument("--message"); p.add_argument("--push", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("context", help="输出注入用的上下文片段")
    p.add_argument("--prompt", default=""); p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("serve", help="启动本地 Web 管理界面")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8787)
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("doctor", help="诊断知识库状态")
    p.set_defaults(func=cmd_doctor)

    for name, mod, desc in (("import-history", "scan_history", "扫描历史会话"),
                            ("import-assets", "import_local_assets", "导入本机资产"),
                            ("export", "reports", "生成 Markdown 报表")):
        pp = sub.add_parser(name, help=desc)
        pp.add_argument("--fast", action="store_true", help=argparse.SUPPRESS)
        pp.set_defaults(func=lambda a, m=mod: __import__(m).main())

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

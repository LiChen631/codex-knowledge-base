#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart hook: inject knowledge-base status + relevant assets.

Contract: JSON on stdin, JSON on stdout, always exit 0.
Emits {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}.
"""
import json
import os
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

KB_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(KB_ROOT / "scripts"))


def emit(text, event="SessionStart"):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "additionalContext": text}},
                     ensure_ascii=False))
    sys.exit(0)


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    try:
        import kbcore as kb
        if not kb.DB_PATH.exists():
            emit("[KB] 全局知识库数据库缺失（%s）。开始实际任务前请先运行：\n"
                 "python \"%s\" init" % (KB_ROOT / "scripts" / "kb.py"))
        cwd = (data.get("cwd") or os.getcwd())
        prompt = ""
        try:
            seen = ""
            if cwd and Path(cwd).exists():
                for name in ("AGENTS.md", "README.md"):
                    p = Path(cwd) / name
                    if p.is_file():
                        seen += p.read_text(encoding="utf-8", errors="ignore")[:400]
            prompt = seen
        except Exception:
            pass
        ctx = kb.build_context(prompt, limit=4, max_chars=1300)
        extra = []
        month = kb.kv_get("last_health_month") or ""
        import datetime as dt
        cur = dt.date.today().strftime("%Y-%m")
        if month != cur:
            extra.append("本月尚未执行健康检查，首次实际任务前请运行："
                         "python \"%s\" health --monthly" % (KB_ROOT / "scripts" / "kb.py"))
        con = kb.connect(readonly=True)
        try:
            row = con.execute("SELECT COUNT(*) n FROM errors WHERE occurrences>=3").fetchone()
            if row and row["n"]:
                top = con.execute("SELECT signature,occurrences FROM errors ORDER BY occurrences"
                                  " DESC LIMIT 2").fetchall()
                extra.append("高频历史故障：%s" % "; ".join(
                    "%s (%d次)" % (t["signature"][:50], t["occurrences"]) for t in top))
        finally:
            con.close()
        if extra:
            ctx += "\n" + "\n".join(extra)
        emit(ctx)
    except Exception as e:  # never break a session
        emit("[KB] 知识库加载异常（%s）。可继续任务，但请先修复：python kb.py doctor" % type(e).__name__)


if __name__ == "__main__":
    main()

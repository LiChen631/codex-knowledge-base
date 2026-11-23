#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook: attach the top matching assets to the prompt."""
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

KB_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(KB_ROOT / "scripts"))

MAX = 900


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt or len(prompt) < 4:
        print(json.dumps({"continue": True}))
        return
    try:
        import kbcore as kb
        hits = kb.search(prompt, limit=3)
    except Exception:
        print(json.dumps({"continue": True}))
        return
    if not hits:
        print(json.dumps({"continue": True}))
        return
    lines = ["[KB] 命中知识库（复用前请核对版本与安装状态）："]
    for h in hits:
        lines.append("- %s [%s] %s/%s :: %s" % (
            h["name"], h["category"], h["install"], h["verify"],
            (h["summary"] or "")[:80].replace("\n", " ")))
    text = "\n".join(lines)[:MAX]
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": text}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

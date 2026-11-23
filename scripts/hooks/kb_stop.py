#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop hook: cheap end-of-turn trail for later mining. Never blocks."""
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


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    try:
        import datetime as dt
        import kbcore as kb
        p = kb.LOGS / "turn_trail.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "session_id": data.get("session_id"),
            "cwd": data.get("cwd"),
            "model": data.get("model"),
            "last": (data.get("last_assistant_message") or "")[:400],
        }
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()

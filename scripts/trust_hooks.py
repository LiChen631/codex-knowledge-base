#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trust_hooks.py -- persist hook trust hashes for our hooks.

Why: this Codex build refuses to run hooks that are not recorded as trusted in
config.toml (`[hooks.state.'<key>'] trusted_hash = "sha256:..."`). The correct
hash is computed by Codex itself and exposed by the `hooks/list` app-server
method, so we ask Codex for it instead of guessing a serialization.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

CODEX = Path(os.path.expanduser("~")) / ".codex"
HOOKS = CODEX / "hooks.json"
CONFIG = CODEX / "config.toml"


def query_hooks(cwd: str) -> list:
    proc = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    def send(o):
        proc.stdin.write(json.dumps(o) + "\n")
        proc.stdin.flush()
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "kb-trust", "version": "1.0"}}})
        time.sleep(1.5)
        send({"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {"cwds": [cwd]}})
        deadline = time.time() + 20
        data = []
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("id") == 2:
                data = (o.get("result") or {}).get("data") or []
                break
        return data
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()


def split_sections(text: str):
    """Split a TOML file into (preamble, [(header, body), ...]) without reformatting it."""
    lines = text.splitlines()
    preamble, sections, cur, buf = [], [], None, []
    for line in lines:
        if re.match(r"^\s*\[", line):
            if cur is not None:
                sections.append((cur, buf))
            cur, buf = line.strip(), []
            continue
        (buf if cur is not None else preamble).append(line)
    if cur is not None:
        sections.append((cur, buf))
    return preamble, sections


def write_trust(entries: dict) -> str:
    if CONFIG.exists():
        shutil.copy(CONFIG, str(CONFIG) + ".bak-kb")
    text = CONFIG.read_text(encoding="utf-8") if CONFIG.exists() else ""
    preamble, sections = split_sections(text)
    state = {}
    keep = []
    for header, body in sections:
        m = re.match(r"^\[hooks\.state\.'(.*)'\]$", header)
        if m:
            state[m.group(1)] = body
        else:
            keep.append((header, body))
    for key, h in entries.items():
        state[key] = ['trusted_hash = "%s"' % h]
    out = preamble
    for header, body in keep:
        out.append(header)
        out.extend(body)
    for key in sorted(state):
        out.append("[hooks.state.'%s']" % key)
        out.extend(state[key])
    CONFIG.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return str(CONFIG)


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 else str(Path.cwd())
    if not HOOKS.exists():
        sys.exit("hooks.json 不存在")
    data = query_hooks(cwd)
    if not data:
        sys.exit("未能从 app-server 获取 hooks/list（Codex 可能正在重启）")
    entries, report = {}, []
    target = str(HOOKS).lower()
    for entry in data:
        for h in entry.get("hooks", []):
            if (h.get("sourcePath") or "").lower() != target:
                continue
            _e = json.dumps(h, ensure_ascii=False)
            key, hsh = h.get("key"), h.get("currentHash")
            if key and hsh:
                entries[key] = hsh
                report.append((h.get("eventName"), h.get("trustStatus"), hsh))
    if not entries:
        sys.exit("hooks.json 中没有可信任的 hook")
    path = write_trust(entries)
    print("已写入信任哈希 ->", path)
    for ev, st, hsh in report:
        print("  %-18s %-10s %s" % (ev, st, hsh[:24] + "..."))
    # verify
    data = query_hooks(cwd)
    still = [h["key"] for e in data for h in e.get("hooks", [])
             if h.get("trustStatus") != "trusted" and (h.get("sourcePath") or "").lower() == target]
    print("校验:", "全部 trusted ✅" if not still else ("仍未信任: %s" % still))


if __name__ == "__main__":
    main()

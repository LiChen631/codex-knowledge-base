#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve.py -- local-only HTTP API + static UI for the knowledge base.

Security posture
---------------
* Binds to 127.0.0.1 by default (loopback only).
* Optional shared-secret token via KB_DASH_TOKEN (sent as X-KB-Token).
* Read-only unless --allow-write is passed; write endpoints require the token.
* No external CDN, no telemetry, no outbound requests.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import kbcore as kb  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
TOKEN = os.environ.get("KB_DASH_TOKEN", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "CodexKB/1.0"
    allow_write = False

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------- helpers
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _auth(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("X-KB-Token", "") == TOKEN

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 2_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # -------------------------------------------------------------- routing
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p == "/api/stats":
                return self._json(kb.stats())
            if p == "/api/health":
                import health_check
                return self._json(health_check.collect())
            if p == "/api/assets":
                return self._json(self.assets(q))
            if p.startswith("/api/asset/"):
                aid = urllib.parse.unquote(p[len("/api/asset/"):])
                a = kb.get_asset(aid)
                return self._json(a) if a else self._json({"error": "not found"}, 404)
            if p == "/api/search":
                return self._json(kb.search(q.get("q", [""])[0],
                                            category=(q.get("category") or [None])[0],
                                            limit=int((q.get("limit") or ["20"])[0]),
                                            include_history=True))
            if p == "/api/docs":
                return self._json(kb.search_docs(q.get("q", [""])[0],
                                                 limit=int((q.get("limit") or ["20"])[0])))
            if p == "/api/commands":
                return self._json(self.rows("SELECT signature,uses,ok_count,fail_count,risk,"
                                            "sample,cwds,first_seen,last_seen FROM commands"
                                            " ORDER BY uses DESC LIMIT ?",
                                            (int((q.get("limit") or ["200"])[0]),)))
            if p == "/api/errors":
                return self._json(self.rows("SELECT signature,occurrences,first_seen,last_seen,"
                                            "resolved,fix_summary,error_text FROM errors"
                                            " ORDER BY occurrences DESC LIMIT ?",
                                            (int((q.get("limit") or ["200"])[0]),)))
            if p == "/api/history":
                return self._json(self.rows("SELECT source_id,thread_title,kind,name,detail,ts,cwd"
                                            " FROM history_items ORDER BY id DESC LIMIT ?",
                                            (int((q.get("limit") or ["100"])[0]),)))
            if p == "/api/gitlog":
                import subprocess
                n = int((q.get("limit") or ["40"])[0])
                r = subprocess.run(["git", "-C", str(kb.ROOT), "log",
                                    "-n", str(n), "--date=format:%Y-%m-%d %H:%M",
                                    "--pretty=format:%h|%ad|%s"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
                rows = []
                for line in (r.stdout or "").splitlines():
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        rows.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
                st = subprocess.run(["git", "-C", str(kb.ROOT), "status", "--porcelain"],
                                    capture_output=True, text=True).stdout
                rem = subprocess.run(["git", "-C", str(kb.ROOT), "remote", "get-url", "origin"],
                                     capture_output=True, text=True).stdout.strip()
                return self._json({"commits": rows,
                                   "dirty": len([x for x in st.splitlines() if x.strip()]),
                                   "remote": rem})
            if p.startswith("/api/deps/"):
                aid = urllib.parse.unquote(p[len("/api/deps/"):])
                return self._json({
                    "asset": aid,
                    "depends_on": kb.dependency_chain(aid, "down", 6),
                    "required_by": kb.dependency_chain(aid, "up", 6),
                })
            if p == "/api/sources":
                return self._json(self.rows("SELECT * FROM history_sources"))
            if p == "/api/events":
                return self._json(self.rows("SELECT e.asset_id,a.name,e.event,e.detail,e.happened_at"
                                            " FROM asset_events e LEFT JOIN assets a ON a.id=e.asset_id"
                                            " ORDER BY e.id DESC LIMIT ?",
                                            (int((q.get("limit") or ["80"])[0]),)))
            if p == "/healthz":
                i = kb.integrity()
                return self._json({"ok": i.get("quick_check") == "ok", **i})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        if not self.allow_write:
            return self._json({"error": "写操作已禁用（启动时加 --allow-write）"}, 403)
        if not self._auth():
            return self._json({"error": "unauthorized"}, 401)
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/asset":
                con = kb.connect()
                try:
                    aid = kb.upsert_asset(con, body, tags=body.get("tags"),
                                          event=("noted", "Web 界面保存"))
                finally:
                    con.close()
                return self._json({"id": aid})
            if u.path.startswith("/api/verify/"):
                aid = urllib.parse.unquote(u.path[len("/api/verify/"):])
                a = kb.get_asset(aid)
                if not a:
                    return self._json({"error": "not found"}, 404)
                cmd = body.get("cmd") or a.get("install_command") or ""
                if not cmd:
                    return self._json({"error": "缺少可执行验证命令"}, 400)
                try:
                    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120,
                                       encoding="utf-8", errors="replace")
                    ok = r.returncode == 0
                    out = (r.stdout or "") + (r.stderr or "")
                except Exception as e:
                    ok, out = False, str(e)
                con = kb.connect()
                try:
                    kb.add_verification(con, aid, "web", cmd, "pass" if ok else "fail", out)
                    kb.add_event(con, aid, "verified" if ok else "failed", cmd)
                finally:
                    con.close()
                return self._json({"ok": ok, "output": out[-2000:]})
            if u.path == "/api/health":
                import health_check
                return self._json(health_check.run(monthly=bool(body.get("monthly"))))
            if u.path == "/api/backup":
                p = kb.backup(body.get("tag") or "web")
                return self._json({"path": str(p), "bytes": p.stat().st_size})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # ---------------------------------------------------------------- data
    def assets(self, q):
        sql = ("SELECT a.id,a.name,a.category,a.summary,a.install_status,a.verify_status,"
               "a.lifecycle_status,a.evidence_level,a.version,a.path,"
               " (SELECT group_concat(tag,' ') FROM asset_tags WHERE asset_id=a.id) tags"
               " FROM assets a WHERE 1=1")
        args = []
        cat = (q.get("category") or [None])[0]
        state = (q.get("state") or [None])[0]
        kw = (q.get("q") or [None])[0]
        if cat:
            sql += " AND a.category=?"; args.append(cat)
        if state == "verified":
            sql += " AND a.verify_status='verified'"
        elif state == "unverified":
            sql += " AND a.verify_status='unverified'"
        elif state == "installed":
            sql += " AND a.install_status='installed'"
        if kw:
            sql += " AND (a.name LIKE ? OR a.summary LIKE ?)"; args += ["%" + kw + "%"] * 2
        sql += " ORDER BY a.category, a.name LIMIT ?"
        args.append(int((q.get("limit") or ["500"])[0]))
        return self.rows(sql, tuple(args))

    def rows(self, sql, args=()):
        con = kb.connect(readonly=True)
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def _static(self, name: str):
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def main(host="127.0.0.1", port=8787, open_browser=False, allow_write=False):
    Handler.allow_write = allow_write
    srv = ThreadingHTTPServer((host, port), Handler)
    url = "http://%s:%d/" % (host, port)
    print("知识库管理界面: %s" % url)
    print("只读模式" if not allow_write else "读写模式（POST 需 X-KB-Token，若设置）")
    print("按 Ctrl+C 停止")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--allow-write", action="store_true")
    a = ap.parse_args()
    main(a.host, a.port, a.open, a.allow_write)

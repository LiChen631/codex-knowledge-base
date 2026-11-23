#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the knowledge base core. Run: python tests/test_kb.py -v"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KB / "scripts"))
import kbcore as kb  # noqa: E402


class Base(unittest.TestCase):
    """Each class gets an isolated KB_ROOT so tests never touch the real database."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kbtest-"))
        (cls.tmp / "database" / "migrations").mkdir(parents=True)
        for f in (KB / "database" / "migrations").glob("*.sql"):
            shutil.copy(f, cls.tmp / "database" / "migrations" / f.name)
        cls._env = dict(os.environ)
        os.environ["KB_ROOT"] = str(cls.tmp)
        os.environ["KB_DB"] = str(cls.tmp / "database" / "knowledge.db")
        # rebind module paths
        kb.ROOT = cls.tmp
        kb.DB_PATH = Path(os.environ["KB_DB"])
        kb.MIGRATIONS = cls.tmp / "database" / "migrations"
        kb.REPORTS = cls.tmp / "reports"
        kb.LOGS = cls.tmp / "logs"
        kb.BACKUPS = cls.tmp / "backups"
        kb.migrate()

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls._env)
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestSchema(Base):
    def test_tables_exist(self):
        con = kb.connect(readonly=True)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        for t in ("assets", "asset_tags", "asset_links", "asset_events", "verifications",
                  "history_items", "commands", "errors", "docs", "health_runs"):
            self.assertIn(t, names)

    def test_migration_recorded(self):
        con = kb.connect(readonly=True)
        row = con.execute("SELECT version FROM schema_migrations").fetchone()
        con.close()
        self.assertEqual(row[0], "V001")

    def test_fts_trigram_available(self):
        con = kb.connect(readonly=True)
        con.execute("SELECT count(*) FROM assets_fts WHERE assets_fts MATCH '\"abc\"'").fetchone()
        con.close()


class TestAssets(Base):
    def test_upsert_and_search(self):
        con = kb.connect()
        aid = kb.upsert_asset(con, {
            "name": "数学建模助手", "category": "skill",
            "summary": "用于数学建模论文写作与图表生成", "use_cases": "竞赛论文",
            "install_status": "installed", "verify_status": "verified",
            "lifecycle_status": "in_use", "evidence_level": "B",
        }, tags=["mathmodel", "paper"])
        con.close()
        self.assertEqual(aid, "skill:数学建模助手")
        hits = kb.search("数学建模 论文")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["name"], "数学建模助手")

    def test_update_is_partial(self):
        con = kb.connect()
        kb.upsert_asset(con, {"name": "partial-test", "category": "script",
                             "summary": "initial", "notes": "keep me"})
        kb.upsert_asset(con, {"name": "partial-test", "category": "script",
                             "summary": "updated"})
        con.close()
        a = kb.get_asset("script:partial-test")
        self.assertEqual(a["summary"], "updated")
        self.assertEqual(a["notes"], "keep me")

    def test_duplicate_name_same_category_merges(self):
        con = kb.connect()
        kb.upsert_asset(con, {"name": "dup", "category": "software", "summary": "a"})
        kb.upsert_asset(con, {"name": "dup", "category": "software", "summary": "b",
                             "id": "software:other-id"})
        n = con.execute("SELECT COUNT(*) FROM assets WHERE name='dup' AND category='software'").fetchone()[0]
        con.close()
        self.assertEqual(n, 1)

    def test_fts_stays_in_sync(self):
        con = kb.connect()
        kb.upsert_asset(con, {"name": "sync-check-xyz", "category": "software",
                             "summary": "unique-token-qqq"})
        con.close()
        hits = kb.search("unique-token-qqq")
        self.assertTrue(any(h["name"] == "sync-check-xyz" for h in hits))

    def test_verification_marks_verified(self):
        con = kb.connect()
        aid = kb.upsert_asset(con, {"name": "verif", "category": "software",
                                    "verify_status": "unverified",
                                    "lifecycle_status": "pending_verification"})
        kb.add_verification(con, aid, "unit", "echo 1", "pass", "1")
        con.close()
        a = kb.get_asset(aid)
        self.assertEqual(a["verify_status"], "verified")

    def test_failed_verification_marks_failed(self):
        con = kb.connect()
        aid = kb.upsert_asset(con, {"name": "verif2", "category": "software"})
        kb.add_verification(con, aid, "unit", "false", "fail", "")
        con.close()
        self.assertEqual(kb.get_asset(aid)["verify_status"], "failed")


class TestSecrets(Base):
    def test_redact_sk_key(self):
        s = "token = sk-abcdefghijklmnopqrstuvwxyz123456"
        self.assertNotIn("abcdefghijklmnop", kb.redact(s))

    def test_redact_keeps_env_reads(self):
        s = 'TOKEN = os.environ.get("KB_DASH_TOKEN", "")'
        self.assertEqual(kb.redact(s), s)

    def test_scan_reports_but_hides_value(self):
        p = self.tmp / "leak.txt"
        p.write_text('api_key = "sk-abcdefghijklmnopqrstuvwx"\n', encoding="utf-8")
        found = kb.scan_secrets([p])
        self.assertTrue(found)
        self.assertNotIn("abcdefghijklmnop", json.dumps(found))
        p.unlink()

    def test_asset_notes_are_redacted(self):
        con = kb.connect()
        aid = kb.upsert_asset(con, {"name": "leaky", "category": "script",
                                   "notes": "key=sk-abcdefghijklmnopqrstuvwx"})
        con.close()
        self.assertNotIn("abcdefghijklmnop", kb.get_asset(aid)["notes"])


class TestOps(Base):
    def test_backup_and_restore(self):
        p = kb.backup("unit")
        self.assertTrue(p.exists() and p.stat().st_size > 0)
        before = kb.stats()["assets"]
        kb.restore(str(p), force=True)
        self.assertEqual(kb.stats()["assets"], before)

    def test_integrity_ok(self):
        self.assertEqual(kb.integrity()["quick_check"], "ok")

    def test_docs_index(self):
        (self.tmp / "docs").mkdir(exist_ok=True)
        (self.tmp / "docs" / "a.md").write_text("# 标题\n\n内容 keyword-abc\n", encoding="utf-8")
        n = kb.index_docs()
        self.assertGreaterEqual(n, 1)
        self.assertTrue(kb.search_docs("keyword-abc"))


class TestCli(Base):
    def test_cli_search_on_isolated_db(self):
        env = dict(os.environ)
        out = subprocess.run([sys.executable, str(KB / "scripts" / "kb.py"), "doctor"],
                             capture_output=True, text=True, env=env, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("quick_check", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

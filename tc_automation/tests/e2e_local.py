#!/usr/bin/env python3
"""End-to-end test with REAL Claude calls, a local samples folder and a fake dashboard.
Run:  python tests/e2e_local.py      (needs ANTHROPIC_API_KEY in .env or the environment)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["TC_BACKOFF_BASE"] = "0.05"

from config import Config  # noqa: E402
import run as runner       # noqa: E402
from fake_dashboard import FakeDashboard  # noqa: E402



class StubAnalyzer:
    """TEST-ONLY stand-in for Claude (set TC_STUB_CLAUDE=1) to verify plumbing when no API key is usable.
    It is deliberately crude; the real prompt quality is only tested with the real Analyzer."""
    def __init__(self, api_key, model, name, email):
        self.email = email.lower()

    def analyze(self, filename, text):
        import re
        from analyzer import Analysis
        low = text.lower()
        is_sub = "has submitted their assignment" in low
        mine = is_sub and re.search(r"learner\s+" + re.escape(self.email), low) is not None
        m = re.search(r"under the topic (.+?) as part of", text)
        c = re.search(r"curriculum for (.+?)\.", text)
        return Analysis(is_sub, mine, "stub", assignment_name=m.group(1) if m else None,
                        course=c.group(1) if c else None, status="Submitted", submission_time="2026-11-13T09:05:12Z")


if os.environ.get("TC_STUB_CLAUDE"):
    runner.Analyzer = StubAnalyzer
    print("*** TC_STUB_CLAUDE=1: using the stub analyzer (plumbing test only) ***")

FAILS = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def make_cfg(api_url, tmp, local_dir):
    cfg = Config.load()
    cfg.source, cfg.local_dir, cfg.api_base = "local", Path(local_dir), api_url
    cfg.state_db, cfg.log_dir = Path(tmp) / "state.db", Path(tmp) / "logs"
    cfg.sender_name = "Rajnish Singh"
    cfg.learner_name = cfg.learner_name or "Rajnish Singh"
    cfg.learner_email = cfg.learner_email or "rajnish13547@gmail.com"
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    runner.setup_logging(cfg)
    return cfg


def main():
    samples = ROOT / "samples"
    print("\n[1] First run over the samples folder (real Claude analysis)")
    with tempfile.TemporaryDirectory() as tmp:
        api = FakeDashboard()
        cfg = make_cfg(api.url, tmp, samples)
        s1 = runner.run(cfg)
        check(s1["pushed"] == 3, "pushed 3 entries (mine_1, mine_2_mixedcase, mine_missing_details) -> got %d" % s1["pushed"])
        check(len(api.entries) == 3, "dashboard holds 3 entries -> %d" % len(api.entries))
        check(s1["empty"] == 1, "empty file skipped")
        check(s1["duplicates"] == 1, "duplicate copy of the same email skipped -> %d" % s1["duplicates"])
        check(s1["errors"] == 0, "no errors")
        texts = "\n".join(e["text"] for e in api.entries)
        check("priya.sharma" not in texts and "rajnish13548" not in texts and "someone.else" not in texts,
              "no other participant's / near-miss / injection email was pushed")
        check("Automate Trainer Central" in texts and "LLM API Costing" in texts, "assignment names extracted")
        print("\n--- sample pushed entry ---\n" + api.entries[0]["text"] + "\n---------------------------")

        print("\n[2] Second run on the same files (R5 no duplicates)")
        before = api.posts
        s2 = runner.run(cfg)
        check(api.posts == before and s2["pushed"] == 0 and s2["files_new"] == 0, "no new POSTs, nothing re-processed")

        print("\n[3] Lost local state: wipe state.db, run again -> server-side marker check prevents duplicates")
        cfg.state_db.unlink()
        s3 = runner.run(cfg)
        check(len(api.entries) == 3, "still exactly 3 entries on the dashboard -> %d" % len(api.entries))
        api.stop()

    print("\n[4] API failures: 503 on first POST, and a lost response (entry stored but client sees 500)")
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as one:
        (Path(one) / "a.txt").write_text((samples / "mine_1.txt").read_text())
        (Path(one) / "b.txt").write_text((samples / "mine_2_mixedcase.txt").read_text())
        api = FakeDashboard()
        api.fail_next_posts, api.ghost_next_posts = 1, 1
        cfg = make_cfg(api.url, tmp, one)
        s = runner.run(cfg)
        check(len(api.entries) == 2 and s["errors"] == 0, "both submissions present exactly once after retries -> %d entries, %d errors" % (len(api.entries), s["errors"]))
        api.stop()

    print("\n[5] Dashboard completely down: run must not crash; files retried on the next run")
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as one:
        (Path(one) / "a.txt").write_text((samples / "mine_1.txt").read_text())
        cfg = make_cfg("http://127.0.0.1:9", tmp, one)   # nothing listens on port 9
        s = runner.run(cfg)
        check(s["errors"] == 1 and s["pushed"] == 0, "error logged, nothing pushed, no crash")
        api = FakeDashboard()
        cfg.api_base = api.url
        s = runner.run(cfg)
        check(s["pushed"] == 1 and len(api.entries) == 1, "next run (API back) pushes it")
        api.stop()

    print("\n[6] Source unreachable (folder missing): run ends cleanly")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg("http://127.0.0.1:9", tmp, Path(tmp) / "nope")
        s = runner.run(cfg)
        check(s["drive_ok"] is False and s["errors"] == 1, "logged source error, exited cleanly")

    print("\nRESULT:", "ALL PASSED" if not FAILS else "%d FAILED: %s" % (len(FAILS), FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

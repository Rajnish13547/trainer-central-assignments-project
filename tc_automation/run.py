#!/usr/bin/env python3
"""Trainer Central -> Dashboard automation. One invocation = one run (the scheduler calls it every 15 minutes).

Flow: list Drive files -> skip already-processed -> Claude analyses each new file (mine? details?) ->
      push mine to POST /api/entries (once) -> record in local SQLite -> log a run summary.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from analyzer import Analysis, AnalysisError, Analyzer
from config import Config
from dashboard import ApiError, Dashboard
from sources import DriveSource, LocalSource, SourceError
from state import State

log = logging.getLogger("tc.run")


def setup_logging(cfg: Config) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fh = RotatingFileHandler(cfg.log_dir / "trainer_central.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    sh = logging.StreamHandler(sys.stdout)
    for h in (fh, sh):
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def dedupe_key(text: str) -> str:
    """Stable identity of a submission: the email's Message ID if present, else a hash of its content.
    Means two Drive files holding the same email produce one dashboard entry."""
    m = re.search(r"Message[ -]?ID:\s*(\S+)", text, re.I)
    if m:
        return "msg-" + re.sub(r"[^A-Za-z0-9._-]", "", m.group(1))[:64]
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return "sha-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def identity_in_text(cfg: Config, a: Analysis, text: str) -> bool:
    """Safety net AFTER Claude's decision: the learner's email or full name must really appear in the file."""
    low = text.lower()
    return cfg.learner_email.lower() in low or cfg.learner_name.lower() in low


def build_entry_text(a: Analysis, file_name: str, marker: str) -> str:
    lines = ["Assignment: %s" % (a.assignment_name or "(not stated in email)")]
    if a.course:
        lines.append("Course: %s" % a.course)
    lines.append("Status: %s" % (a.status or "Submitted"))
    if a.submission_time:
        lines.append("Submitted at: %s" % a.submission_time)
    if a.other_details:
        lines.append("Details: %s" % a.other_details)
    lines.append("Source file: %s" % file_name)
    lines.append(marker)
    return "\n".join(lines)[:10000]


def run(cfg: Config, dry_run: bool = False) -> dict:
    run_id = uuid.uuid4().hex[:8]
    t0 = time.time()
    s = dict(run_id=run_id, started=datetime.now(timezone.utc).isoformat(timespec="seconds"), dry_run=dry_run,
             files_seen=0, files_new=0, matched=0, pushed=0, duplicates=0, not_mine=0, empty=0, unrelated=0,
             errors=0, api_status=None, drive_ok=True)
    log.info("=== run %s start (source=%s, model=%s, dry_run=%s) ===", run_id, cfg.source, cfg.claude_model, dry_run)

    state = State(cfg.state_db)
    source = LocalSource(cfg.local_dir) if cfg.source == "local" else DriveSource(cfg.drive_folder_id, cfg.google_api_key)
    api = Dashboard(cfg.api_base, cfg.api_key)
    analyzer = Analyzer(cfg.anthropic_api_key, cfg.claude_model, cfg.learner_name, cfg.learner_email)
    api_ready = False

    try:
        files = source.list_files()
    except SourceError as e:
        log.error("Could not list files: %s", e)
        s.update(drive_ok=False, errors=1)
        return _finish(cfg, state, s, t0)
    except Exception:
        log.exception("Unexpected error listing files")
        s.update(drive_ok=False, errors=1)
        return _finish(cfg, state, s, t0)

    s["files_seen"] = len(files)
    new = [f for f in files if not state.is_done(f.id, f.md5)]
    s["files_new"] = len(new)
    log.info("Files seen: %d, new/changed: %d", len(files), len(new))
    if len(new) > cfg.max_files_per_run:
        log.warning("Capping this run at %d files (rest next run)", cfg.max_files_per_run)
        new = new[: cfg.max_files_per_run]

    for f in new:
        try:
            text = source.read_text(f)
            if not text.strip():
                log.info("[%s] empty file -> skipped", f.name)
                state.record(f.id, f.name, f.md5, "empty")
                s["empty"] += 1
                continue
            text = text[: cfg.max_chars_per_file]
            key = dedupe_key(text)
            marker = "[ref:%s]" % key

            prior = state.pushed_entry_for_key(key)
            if prior:
                log.info("[%s] same submission (%s) already pushed as entry %s -> skipped", f.name, key, prior)
                state.record(f.id, f.name, f.md5, "duplicate", "same content as an already pushed file", key, prior)
                s["duplicates"] += 1
                continue

            a = analyzer.analyze(f.name, text)
            if not a.is_submission:
                log.info("[%s] not a Trainer Central submission (%s)", f.name, a.reason)
                state.record(f.id, f.name, f.md5, "skipped", a.reason, key)
                s["unrelated"] += 1
                continue
            if not a.is_mine:
                log.info("[%s] submission for another learner (%s)", f.name, a.reason)
                state.record(f.id, f.name, f.md5, "not_mine", a.reason, key)
                s["not_mine"] += 1
                continue
            if not identity_in_text(cfg, a, text):
                log.warning("[%s] Claude said 'mine' but my name/email is not in the file -> rejected (%s)", f.name, a.reason)
                state.record(f.id, f.name, f.md5, "not_mine", "identity not found in text; " + a.reason, key)
                s["not_mine"] += 1
                continue

            s["matched"] += 1
            if not a.assignment_name:
                log.warning("[%s] matched but assignment name missing in email; pushing with what is present", f.name)
            body = build_entry_text(a, f.name, marker)
            log.info("[%s] MATCH assignment=%r status=%r time=%r", f.name, a.assignment_name, a.status, a.submission_time)
            if dry_run:
                log.info("[%s] dry-run: would POST:\n%s", f.name, body)
                continue

            if not api_ready:
                api.wake_up()
                api_ready = True
            existing = api.find_by_marker(marker, cfg.sender_name)
            if existing:
                log.info("[%s] entry already on dashboard (%s) -> not re-posting", f.name, existing.get("id"))
                entry = existing
                s["duplicates"] += 1
            else:
                entry = api.post_entry(body, cfg.sender_name, marker)
                log.info("[%s] POSTED entry id=%s (HTTP %s)", f.name, entry.get("id"), api.last_status)
                s["pushed"] += 1
            s["api_status"] = api.last_status
            state.record(f.id, f.name, f.md5, "pushed", a.reason, key, str(entry.get("id")))
        except (ApiError, AnalysisError, SourceError) as e:
            # Not recorded as processed -> automatically retried on the next run.
            log.error("[%s] failed, will retry next run: %s", f.name, e)
            s["errors"] += 1
            s["api_status"] = api.last_status
        except Exception:
            log.exception("[%s] unexpected error, continuing with next file", f.name)
            s["errors"] += 1

    return _finish(cfg, state, s, t0)


def _finish(cfg: Config, state: State, s: dict, t0: float) -> dict:
    s["seconds"] = round(time.time() - t0, 1)
    log.info("=== run %s done: seen=%d new=%d matched=%d pushed=%d duplicates=%d not_mine=%d unrelated=%d empty=%d "
             "errors=%d api_status=%s (%.1fs) ===", s["run_id"], s["files_seen"], s["files_new"], s["matched"],
             s["pushed"], s["duplicates"], s["not_mine"], s["unrelated"], s["empty"], s["errors"], s["api_status"], s["seconds"])
    with open(cfg.log_dir / "runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(s) + "\n")
    state.close()
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Push my Trainer Central submissions to the dashboard")
    ap.add_argument("--dry-run", action="store_true", help="analyse with Claude but do not POST or write state for matches")
    ap.add_argument("--source", choices=["drive", "local"], help="override DRIVE_SOURCE")
    ap.add_argument("--dir", help="local folder of .txt files (with --source local)")
    args = ap.parse_args(argv)

    cfg = Config.load()
    if args.source:
        cfg.source = args.source
    if args.dir:
        from pathlib import Path
        cfg.local_dir = Path(args.dir)
    setup_logging(cfg)

    problems = cfg.problems()
    if problems:
        for p in problems:
            log.error("Config problem: %s", p)
        return 2

    lock = open(cfg.log_dir / ".run.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("Another run is still in progress; exiting")
        return 0
    try:
        s = run(cfg, dry_run=args.dry_run)
    except Exception:
        log.exception("Run crashed unexpectedly")
        return 1
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

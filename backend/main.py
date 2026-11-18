"""FastAPI wrapper around run.py so the pipeline can be triggered and inspected over HTTP.

Start:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import Config
from run import run, setup_logging

app = FastAPI(title="Trainer Central Automation")

# Comma-separated list of allowed frontend origins; "*" by default.
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])

_run_lock = threading.Lock()


@app.get("/")
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run")
def trigger_run(dry_run: bool = False) -> dict:
    """Run one pass of the pipeline and return its summary. Only one run at a time."""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A run is already in progress")
    try:
        cfg = Config.load()
        setup_logging(cfg)
        problems = cfg.problems()
        if problems:
            raise HTTPException(status_code=500, detail={"config_problems": problems})
        return run(cfg, dry_run=dry_run)
    finally:
        _run_lock.release()


@app.get("/status")
def status(limit: int = 10) -> dict:
    """Return the most recent run summaries from logs/runs.jsonl."""
    cfg = Config.load()
    path = cfg.log_dir / "runs.jsonl"
    runs: list = []
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh.readlines()[-max(1, min(limit, 100)):]:
                try:
                    runs.append(json.loads(line))
                except ValueError:
                    continue
    return {"running": _run_lock.locked(), "runs": runs[::-1]}

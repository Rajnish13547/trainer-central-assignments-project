"""Configuration: everything comes from environment variables (optionally a local .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path) -> None:
    """Tiny .env loader (no dependency). Real environment variables win over the file."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass
class Config:
    anthropic_api_key: str
    claude_model: str
    learner_name: str
    learner_email: str
    sender_name: str
    drive_folder_id: str
    google_api_key: str
    api_base: str
    api_key: str
    source: str            # "drive" or "local"
    local_dir: Path
    state_db: Path
    log_dir: Path
    max_files_per_run: int
    max_chars_per_file: int

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(BASE_DIR / ".env")
        g = os.environ.get
        name = g("LEARNER_NAME", "").strip()
        return cls(
            anthropic_api_key=g("ANTHROPIC_API_KEY", "").strip(),
            claude_model=g("CLAUDE_MODEL", "claude-sonnet-5-5").strip(),
            learner_name=name,
            learner_email=g("LEARNER_EMAIL", "").strip(),
            sender_name=(g("SENDER_NAME", "").strip() or name),
            drive_folder_id=g("DRIVE_FOLDER_ID", "").strip(),
            google_api_key=g("GOOGLE_API_KEY", "").strip(),
            api_base=g("TRAINER_API_BASE", "https://trainer-central-assigments-api.onrender.com").rstrip("/"),
            api_key=g("TRAINER_API_KEY", "").strip(),
            source=g("DRIVE_SOURCE", "drive").strip().lower(),
            local_dir=Path(g("LOCAL_DIR", str(BASE_DIR / "samples"))),
            state_db=Path(g("STATE_DB", str(BASE_DIR / "state.db"))),
            log_dir=Path(g("LOG_DIR", str(BASE_DIR / "logs"))),
            max_files_per_run=int(g("MAX_FILES_PER_RUN", "200")),
            max_chars_per_file=int(g("MAX_CHARS_PER_FILE", "20000")),
        )

    def problems(self, need_claude: bool = True) -> list:
        out = []
        if need_claude and not self.anthropic_api_key:
            out.append("ANTHROPIC_API_KEY is not set")
        if not self.learner_email:
            out.append("LEARNER_EMAIL is not set")
        if not self.learner_name:
            out.append("LEARNER_NAME is not set")
        if not (2 <= len(self.sender_name) <= 100):
            out.append("SENDER_NAME must be 2-100 characters")
        if self.source == "drive" and not (self.drive_folder_id and self.google_api_key):
            out.append("DRIVE_FOLDER_ID and GOOGLE_API_KEY are required when DRIVE_SOURCE=drive")
        return out

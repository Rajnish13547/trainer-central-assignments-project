"""File sources: Google Drive (public/shared folder via Drive API v3 + API key) or a local folder (tests)."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests

log = logging.getLogger("tc.sources")

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
GDOC_MIME = "application/vnd.google-apps.document"


class SourceError(Exception):
    """Source (Drive) unreachable or refused. The run logs it and ends cleanly."""


@dataclass
class SourceFile:
    id: str
    name: str
    modified: str = ""
    md5: Optional[str] = None
    mime: str = ""


class DriveSource:
    def __init__(self, folder_id: str, api_key: str, retries: int = 4, timeout: int = 30):
        self.folder_id, self.api_key, self.retries, self.timeout = folder_id, api_key, retries, timeout

    def _get(self, url: str, params: dict, stream_text: bool = False):
        params = dict(params, key=self.api_key)
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                r = requests.get(url, params=params, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = "HTTP %s" % r.status_code
                elif r.status_code in (401, 403, 404):
                    raise SourceError(
                        "Drive refused access (HTTP %s). Check DRIVE_FOLDER_ID / GOOGLE_API_KEY and that the "
                        "folder is shared 'Anyone with the link'. %s" % (r.status_code, r.text[:200])
                    )
                else:
                    r.raise_for_status()
                    return r
            except requests.RequestException as e:
                last = repr(e)
            wait = min(2 ** attempt, 20)
            log.warning("Drive call failed (%s), attempt %d/%d, retrying in %ds", last, attempt, self.retries, wait)
            time.sleep(wait)
        raise SourceError("Drive unreachable after %d attempts: %s" % (self.retries, last))

    def list_files(self) -> List[SourceFile]:
        out, token = [], None
        while True:
            params = {
                "q": "'%s' in parents and trashed=false" % self.folder_id,
                "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum)",
                "pageSize": 1000,
                "orderBy": "modifiedTime desc",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            data = self._get(DRIVE_FILES, params).json()
            for f in data.get("files", []):
                out.append(SourceFile(f["id"], f.get("name", ""), f.get("modifiedTime", ""),
                                      f.get("md5Checksum"), f.get("mimeType", "")))
            token = data.get("nextPageToken")
            if not token:
                return out

    def read_text(self, f: SourceFile) -> str:
        if f.mime == GDOC_MIME:  # trainer saved it as a Google Doc: export as plain text
            r = self._get("%s/%s/export" % (DRIVE_FILES, f.id), {"mimeType": "text/plain"})
        else:
            r = self._get("%s/%s" % (DRIVE_FILES, f.id), {"alt": "media", "supportsAllDrives": "true"})
        r.encoding = r.encoding or "utf-8"
        return r.content.decode("utf-8", errors="replace")


class LocalSource:
    """Reads *.txt from a local folder. Used for tests and offline demos."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def list_files(self) -> List[SourceFile]:
        if not self.dir.is_dir():
            raise SourceError("Local folder not found: %s" % self.dir)
        files = sorted(self.dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            SourceFile(p.name, p.name, str(p.stat().st_mtime), hashlib.md5(p.read_bytes()).hexdigest(), "text/plain")
            for p in files if p.is_file()
        ]

    def read_text(self, f: SourceFile) -> str:
        return (self.dir / f.id).read_bytes().decode("utf-8", errors="replace")

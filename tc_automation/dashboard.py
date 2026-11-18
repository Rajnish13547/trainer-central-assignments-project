"""Client for the Trainer Central dashboard API (GET /health, GET/POST /api/entries)."""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger("tc.api")
BACKOFF = float(os.environ.get("TC_BACKOFF_BASE", "5"))


class ApiError(Exception):
    def __init__(self, msg: str, retryable: bool = False):
        super().__init__(msg)
        self.retryable = retryable


class Dashboard:
    def __init__(self, base: str, api_key: str = "", retries: int = 4, timeout: int = 90):
        self.base, self.retries, self.timeout = base.rstrip("/"), retries, timeout
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["x-api-key"] = api_key
        self.last_status: Optional[int] = None

    def _request(self, method: str, path: str, retries: Optional[int] = None, **kw):
        """Retries timeouts, connection errors, 429 and 5xx with backoff. Other 4xx fail immediately."""
        tries = retries or self.retries
        url, last = self.base + path, None
        for attempt in range(1, tries + 1):
            try:
                r = requests.request(method, url, headers=self.headers, timeout=self.timeout, **kw)
                self.last_status = r.status_code
                if r.status_code in (200, 201):
                    return r
                last = "HTTP %d %s" % (r.status_code, r.text[:200])
                if r.status_code != 429 and r.status_code < 500:
                    raise ApiError("%s %s -> %s" % (method, path, last), retryable=False)
            except requests.RequestException as e:
                last = repr(e)
            if attempt < tries:
                wait = min(BACKOFF * attempt, 30)
                log.warning("%s %s failed (%s), attempt %d/%d, retrying in %ds", method, path, last, attempt, tries, wait)
                time.sleep(wait)
        raise ApiError("%s %s failed after %d attempt(s): %s" % (method, path, tries, last), retryable=True)

    def wake_up(self) -> None:
        """Render's free tier sleeps; the first call can take ~1 minute. Long timeout + retries."""
        t0 = time.time()
        self._request("GET", "/health")
        log.info("API healthy (%.1fs)", time.time() - t0)

    def list_entries(self, limit: int = 100, skip: int = 0, q: Optional[str] = None) -> dict:
        params = {"limit": limit, "skip": skip}
        if q:
            params["q"] = q
        data = self._request("GET", "/api/entries", params=params).json()
        if isinstance(data, list):
            data = {"total": len(data), "items": data}
        return data

    def find_by_marker(self, marker: str, sender: str) -> Optional[dict]:
        """Look for my [ref:...] marker on the dashboard: server-side search (q) first, then a paged scan."""
        def hit(items):
            return next((i for i in items if marker in (i.get("text") or "") and i.get("sender") == sender), None)

        found = hit(self.list_entries(q=marker).get("items", []))
        if found:
            return found
        skip = 0
        while True:                     # paged scan in case q does not search the text field
            page = self.list_entries(limit=100, skip=skip)
            items = page.get("items", [])
            found = hit(items)
            if found or not items:
                return found
            skip += len(items)
            if skip >= page.get("total", 0):
                return None

    def post_entry(self, text: str, sender: str, marker: str) -> dict:
        """POST once; if the outcome is unclear (timeout/5xx), look for the entry before retrying."""
        if not (1 <= len(text) <= 10000):
            raise ApiError("text must be 1-10000 characters (got %d)" % len(text))
        if not (2 <= len(sender) <= 100):
            raise ApiError("sender must be 2-100 characters")
        payload = {"text": text, "sender": sender}
        for attempt in range(1, self.retries + 1):
            try:
                return self._request("POST", "/api/entries", retries=1, json=payload).json()
            except ApiError as e:
                if not e.retryable or attempt == self.retries:
                    raise
                log.warning("POST attempt %d/%d unclear (%s); checking for an existing entry", attempt, self.retries, e)
                time.sleep(min(BACKOFF * attempt, 30))
                try:
                    existing = self.find_by_marker(marker, sender)
                except ApiError:
                    existing = None
                if existing:
                    log.info("Entry %s already exists; not re-posting", existing.get("id"))
                    return existing
        raise ApiError("POST failed", retryable=True)

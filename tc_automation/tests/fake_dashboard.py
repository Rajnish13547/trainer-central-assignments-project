"""In-process fake of the Trainer Central API for offline tests (same shapes as the real one)."""
import json
import threading
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeDashboard:
    def __init__(self):
        self.entries, self.posts, self.fail_next_posts, self.ghost_next_posts = [], 0, 0, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, {"status": "ok"})
                if self.path.startswith("/api/entries"):
                    qs = parse_qs(urlparse(self.path).query)
                    limit, skip = int(qs.get("limit", [100])[0]), int(qs.get("skip", [0])[0])
                    items = list(reversed(outer.entries))
                    if "q" in qs:
                        items = [e for e in items if qs["q"][0].lower() in (e["text"] + e["sender"]).lower()]
                    return self._send(200, {"total": len(items), "items": items[skip:skip + limit]})
                self._send(404, {"detail": "nf"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.posts += 1
                if outer.fail_next_posts > 0:           # plain failure, nothing stored
                    outer.fail_next_posts -= 1
                    return self._send(503, {"detail": "unavailable"})
                if not (1 <= len(body.get("text", "")) <= 10000) or not (2 <= len(body.get("sender", "")) <= 100):
                    return self._send(422, {"detail": "validation"})
                e = {"id": "e%04d" % (len(outer.entries) + 1), "text": body["text"], "sender": body["sender"],
                     "created_at": "2026-11-13T00:00:00"}
                outer.entries.append(e)
                if outer.ghost_next_posts > 0:           # stored, but the client sees a 500 (lost response)
                    outer.ghost_next_posts -= 1
                    return self._send(500, {"detail": "boom"})
                self._send(201, e)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()

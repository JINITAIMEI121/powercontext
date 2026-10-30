"""NoteSeek web app: a tiny local research notebook with seekdb hybrid search."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import notesdb

ROOT = Path(__file__).parent
WEB = ROOT / "web"


class Handler(BaseHTTPRequestHandler):
    server_version = "NoteSeek/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[noteseek] {self.address_string()} {format % args}")

    def _json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, name: str, content_type: str) -> None:
        path = WEB / name
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json(200, {"status": "ok", "notes": notesdb.count_notes(), "engine": "seekdb"})
            return
        if parsed.path == "/api/notes":
            self._json(200, {"notes": notesdb.list_notes()})
            return
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            limit = int(parse_qs(parsed.query).get("limit", ["8"])[0] or 8)
            try:
                results = notesdb.search_notes(query, limit=limit)
            except Exception as error:
                self._json(500, {"error": str(error)})
                return
            self._json(200, {"query": query, "count": len(results), "results": results})
            return
        if parsed.path in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/style.css":
            self._file("style.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._file("app.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/demo-data.js":
            self._file("demo-data.js", "text/javascript; charset=utf-8")
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/notes":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            note = notesdb.add_note(str(payload.get("title", "")), str(payload.get("body", "")))
        except (ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
            return
        except Exception as error:
            self._json(500, {"error": str(error)})
            return
        self._json(201, {"note": note})


def main() -> None:
    notesdb.init()
    created = notesdb.seed_defaults()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"NoteSeek running at http://127.0.0.1:{port} (seeded {created} notes, {notesdb.count_notes()} total)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        notesdb.shutdown()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.agent import get_dialog_cases, get_recipes, get_users, recommend


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "recipe_count": len(get_recipes()), "user_count": len(get_users())})
            return
        if parsed.path == "/api/users":
            users = [user.raw for user in get_users()]
            self._json({"users": users})
            return
        if parsed.path == "/api/cases":
            self._json({"cases": get_dialog_cases()})
            return
        self._static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/recommend":
            self._json({"error": "not_found"}, status=404)
            return
        try:
            body = self._read_json()
            user_id = body.get("user_id")
            messages = body.get("messages")
            if messages is None and body.get("message"):
                messages = [body["message"]]
            if not isinstance(messages, list) or not all(isinstance(item, str) for item in messages):
                self._json({"error": "messages must be a string list"}, status=400)
                return
            result = recommend(int(user_id) if user_id not in (None, "") else None, messages)
            self._json(result)
        except Exception as exc:
            self._json({"error": "internal_error", "detail": str(exc)}, status=500)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        try:
            raw = payload.decode("utf-8")
        except UnicodeDecodeError:
            raw = payload.decode("gbk")
        return json.loads(raw or "{}")

    def _json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _static(self, path: str):
        if path == "/":
            path = "/index.html"
        target = (PUBLIC / path.lstrip("/")).resolve()
        if not str(target).startswith(str(PUBLIC.resolve())) or not target.exists() or target.is_dir():
            self._json({"error": "not_found"}, status=404)
            return
        content = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            mime = "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("fangtaiRobot running at http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()

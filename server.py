from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.agent import get_dialog_cases, get_recipes, get_users, recommend
from app.audit_jobs import manager as audit_jobs
from app.scenario_agents import agent_candidates_to_audit_scenarios, generate_reviewed_candidates
from app.session_store import store


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
        if parsed.path == "/api/audit/jobs":
            self._json({"jobs": audit_jobs.list_jobs()})
            return
        session_id = _session_history_id(parsed.path)
        if session_id:
            try:
                self._json(store.history(session_id))
            except KeyError:
                self._json({"error": "session_not_found"}, status=404)
            return
        job_id = _audit_job_id(parsed.path)
        if job_id:
            try:
                self._json(audit_jobs.get_job(job_id))
            except KeyError:
                self._json({"error": "audit_job_not_found"}, status=404)
            return
        self._static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/audit/jobs":
            try:
                body = self._read_json()
                scenarios = body.get("scenarios")
                if scenarios is not None and not isinstance(scenarios, list):
                    self._json({"error": "scenarios must be a list"}, status=400)
                    return
                if scenarios is None and body.get("source") == "agent_generated":
                    count = int(body.get("count") or 20)
                    seed = int(body.get("seed") or 20260725)
                    candidates = generate_reviewed_candidates(seed=seed, count=count)
                    scenarios = agent_candidates_to_audit_scenarios(candidates)
                self._json(audit_jobs.start_job(scenarios))
            except Exception as exc:
                self._json({"error": "internal_error", "detail": str(exc)}, status=500)
            return

        job_id = _audit_cancel_job_id(parsed.path)
        if job_id:
            try:
                self._json(audit_jobs.cancel_job(job_id))
            except KeyError:
                self._json({"error": "audit_job_not_found"}, status=404)
            return

        if parsed.path != "/api/recommend":
            self._json({"error": "not_found"}, status=404)
            return
        try:
            body = self._read_json()
            user_id = body.get("user_id")
            session_id = body.get("session_id")
            rollback_to = body.get("rollback_to")
            messages = body.get("messages")
            if messages is None and body.get("message"):
                messages = [body["message"]]
            if not isinstance(messages, list) or not all(isinstance(item, str) for item in messages):
                self._json({"error": "messages must be a string list"}, status=400)
                return
            if rollback_to is not None and (type(rollback_to) is not int or rollback_to < 1):
                self._json({"error": "rollback_version_not_found"}, status=400)
                return
            result = recommend(
                int(user_id) if user_id not in (None, "") else None,
                messages,
                session_id=session_id if session_id not in ("", None) else None,
                rollback_to=rollback_to,
            )
            self._json(result)
        except ValueError as exc:
            self._json({"error": str(exc)}, status=400)
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


def _audit_job_id(path: str) -> str | None:
    prefix = "/api/audit/jobs/"
    if not path.startswith(prefix):
        return None
    suffix = path[len(prefix):].strip("/")
    if not suffix or "/" in suffix:
        return None
    return suffix


def _session_history_id(path: str) -> str | None:
    prefix = "/api/sessions/"
    suffix = path[len(prefix):] if path.startswith(prefix) else ""
    marker = "/history"
    if not suffix.endswith(marker):
        return None
    session_id = suffix[: -len(marker)].strip("/")
    return session_id or None


def _audit_cancel_job_id(path: str) -> str | None:
    prefix = "/api/audit/jobs/"
    suffix = path[len(prefix):] if path.startswith(prefix) else ""
    if not suffix.endswith("/cancel"):
        return None
    job_id = suffix[: -len("/cancel")].strip("/")
    return job_id or None


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"fangtaiRobot running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

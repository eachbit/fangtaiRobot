from __future__ import annotations

import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import Handler


SCENARIO = {
    "name": "接口审计-花生过敏",
    "user_id": None,
    "messages": ["我对花生过敏，推荐3道晚饭"],
    "forbid": ["花生"],
    "expect_count": 3,
}


def _json_request(base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_audit_job_http_api_starts_and_reports_job():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        created = _json_request(base_url, "POST", "/api/audit/jobs", {"scenarios": [SCENARIO]})
        assert created["job_id"]
        assert created["progress"]["total"] == 1

        deadline = time.time() + 10
        current = created
        while time.time() < deadline:
            current = _json_request(base_url, "GET", f"/api/audit/jobs/{created['job_id']}")
            if current["status"] == "completed":
                break
            time.sleep(0.05)

        assert current["status"] == "completed"
        assert current["summary"]["total"] == 1
        assert current["summary"]["official_report"]["max_score"] == 100
        assert current["records"][0]["name"] == "接口审计-花生过敏"
        assert current["records"][0]["answer"]

        listed = _json_request(base_url, "GET", "/api/audit/jobs")
        assert listed["jobs"][0]["job_id"] == created["job_id"]
        assert "records" not in listed["jobs"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_audit_job_http_api_can_start_agent_generated_job():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        created = _json_request(
            base_url,
            "POST",
            "/api/audit/jobs",
            {"source": "agent_generated", "count": 10, "seed": 20260725},
        )
        assert created["progress"]["total"] == 10

        deadline = time.time() + 10
        current = created
        while time.time() < deadline:
            current = _json_request(base_url, "GET", f"/api/audit/jobs/{created['job_id']}")
            if current["status"] == "completed":
                break
            time.sleep(0.05)

        assert current["status"] == "completed"
        assert current["summary"]["total"] == 10
        assert current["summary"]["official_report"]["max_score"] == 100
        assert current["records"][0]["debug"]["source"] == "agent_generated"
        assert "agent_review" in current["records"][0]["debug"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    test_audit_job_http_api_starts_and_reports_job()
    test_audit_job_http_api_can_start_agent_generated_job()
    print("ok: audit job HTTP API")


if __name__ == "__main__":
    main()

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from .audit_runner import DEFAULT_SCENARIOS, evaluate_scenario


class AuditJobManager:
    def __init__(self, max_jobs: int = 20):
        self.max_jobs = max_jobs
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cancel_events: dict[str, Event] = {}
        self._lock = Lock()

    def start_job(self, scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        normalized = [dict(item) for item in (scenarios or DEFAULT_SCENARIOS)]
        job_id = uuid4().hex
        now = time.time()
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "progress": {"total": len(normalized), "completed": 0},
            "summary": {"total": len(normalized), "passed": 0, "failed": 0, "duration_ms": 0},
            "records": [],
        }
        cancel_event = Event()
        with self._lock:
            self._jobs[job_id] = job
            self._cancel_events[job_id] = cancel_event
            self._trim_jobs()
        thread = Thread(target=self._run_job, args=(job_id, normalized, cancel_event), daemon=True)
        thread.start()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return self._snapshot(job, include_records=True)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._snapshot(job, include_records=False) for job in reversed(self._jobs.values())]

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            event = self._cancel_events.get(job_id)
            if event:
                event.set()
            if job["status"] in {"queued", "running"}:
                job["status"] = "canceling"
                job["updated_at"] = time.time()
            return self._snapshot(job, include_records=True)

    def _run_job(self, job_id: str, scenarios: list[dict[str, Any]], cancel_event: Event) -> None:
        start = time.perf_counter()
        self._update(job_id, status="running")
        for scenario in scenarios:
            if cancel_event.is_set():
                self._update(job_id, status="canceled")
                break
            record = evaluate_scenario(scenario)
            self._append_record(job_id, record, start)
        else:
            self._update(job_id, status="completed")
        self._finish_duration(job_id, start)

    def _append_record(self, job_id: str, record: dict[str, Any], start: float) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["records"].append(record)
            job["progress"]["completed"] = len(job["records"])
            job["summary"] = self._build_summary(job["records"], job["progress"]["total"], start)
            job["updated_at"] = time.time()

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(values)
            job["updated_at"] = time.time()

    def _finish_duration(self, job_id: str, start: float) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["summary"] = self._build_summary(job["records"], job["progress"]["total"], start)
            job["updated_at"] = time.time()

    def _build_summary(self, records: list[dict[str, Any]], total: int, start: float) -> dict[str, Any]:
        failed = sum(1 for record in records if record["status"] == "failed")
        completed = len(records)
        return {
            "total": total,
            "passed": completed - failed,
            "failed": failed,
            "duration_ms": int(round((time.perf_counter() - start) * 1000)),
        }

    def _trim_jobs(self) -> None:
        while len(self._jobs) > self.max_jobs:
            job_id, _ = self._jobs.popitem(last=False)
            self._cancel_events.pop(job_id, None)

    def _snapshot(self, job: dict[str, Any], include_records: bool) -> dict[str, Any]:
        snapshot = {
            "job_id": job["job_id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "progress": dict(job["progress"]),
            "summary": dict(job["summary"]),
        }
        if include_records:
            snapshot["records"] = list(job["records"])
        return snapshot


manager = AuditJobManager()

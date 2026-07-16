# Autonomous Evaluation and Issue Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable multi-round evaluation command that stores deterministic failures as deduplicated, stateful issue records under the existing `artifacts/evaluation/` directory.

**Architecture:** Keep orchestration and persistence separate. `EvaluationRunner` exposes a sanitized scenario-context snapshot after each run; `IssueRegistry` owns fingerprints, atomic issue/index writes, status transitions and regression-candidate export; `AutonomousCycle` owns round scheduling, resume state and aggregate summaries. Thin CLI scripts parse arguments and delegate to these modules.

**Tech Stack:** Python 3.11 standard library, `dataclasses`, strict JSON serialization, `unittest`, existing `EvaluationRunner`, existing `Scenario`/`EvaluationReport` schemas.

---

## File Map

- Modify `tests/evaluation/runner.py`: retain a sanitized context snapshot for scenarios evaluated in the latest run.
- Create `tests/evaluation/issue_registry.py`: issue schema validation, fingerprinting, locking, atomic persistence, status changes and regression export.
- Create `tests/evaluation/autonomous_cycle.py`: cycle state, round execution, resume behavior and aggregate summaries.
- Create `scripts/run_autonomous_cycle.py`: autonomous-cycle CLI.
- Create `scripts/manage_evaluation_issue.py`: issue status/export CLI.
- Create `tests/test_issue_registry.py`: registry and safety tests.
- Create `tests/test_autonomous_cycle.py`: orchestration and resume tests.
- Modify `tests/test_evaluation_runner.py`: context redaction tests.
- Modify `README.md`: operating instructions and storage layout.

### Task 1: Expose Sanitized Scenario Context

**Files:**
- Modify: `tests/evaluation/runner.py`
- Modify: `tests/test_evaluation_runner.py`

- [ ] **Step 1: Write failing tests for public and holdout context**

Add tests that run a ten-case runner and assert `runner.scenario_context` contains the non-holdout scenario's bucket, intent, expectation and strict `Scenario.to_dict()` source. Add a holdout test asserting its context contains only `holdout`, `scenario_hash` and no messages, persona, expectation or scenario object.

```python
self.assertEqual(
    set(runner.scenario_context["public-case"]),
    {"holdout", "health_bucket", "intent", "expectation", "scenario"},
)
self.assertEqual(
    set(runner.scenario_context["private-case"]),
    {"holdout", "scenario_hash"},
)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_evaluation_runner -v
```

Expected: FAIL because `EvaluationRunner` has no `scenario_context` attribute.

- [ ] **Step 3: Implement context capture at the scenario-loading boundary**

Initialize `self.scenario_context: dict[str, dict[str, Any]] = {}` in `__init__`. In `run_count`, immediately after `_load_scenarios`, build the mapping using `self.source_metadata`:

```python
def _scenario_context_payload(self, scenario: Scenario) -> dict[str, Any]:
    source = self.source_metadata[scenario.scenario_id]
    if source.get("holdout") is True:
        return {
            "holdout": True,
            "scenario_hash": str(source["scenario_hash"]),
        }
    return {
        "holdout": False,
        "health_bucket": scenario.persona.primary_bucket,
        "intent": scenario.intent,
        "expectation": scenario.expectation.to_dict(),
        "scenario": scenario.to_dict(),
    }
```

Return fresh JSON-compatible dictionaries and do not expose `Scenario` objects.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_evaluation_runner -v
```

Expected: all evaluation-runner tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/evaluation/runner.py tests/test_evaluation_runner.py
git commit -m "feat: expose sanitized evaluation context"
```

### Task 2: Implement the Issue Registry

**Files:**
- Create: `tests/evaluation/issue_registry.py`
- Create: `tests/test_issue_registry.py`

- [ ] **Step 1: Write failing tests for fingerprints, merge, reopen and safety**

Create helpers producing `FailureRecord`, `Violation` and context dictionaries. Cover:

The representative merge test constructs two `FailureRecord` objects with the same minimized message and violation code but different seed, commit and elapsed time, ingests both, and asserts one issue file with two occurrences:

```python
violation = Violation("constraint.forbidden_term", "blocking", "blocked", {})
first = FailureRecord("s-1", 10, "aaa", ("原始一",), ("不要花生",), (violation,), 1.0)
second = FailureRecord("s-2", 20, "bbb", ("原始二",), ("不要花生",), (violation,), 999.0)
context = {
    "holdout": False,
    "health_bucket": "single_condition",
    "intent": "hard_constraint",
    "expectation": {"forbidden_terms": ["花生"]},
    "scenario": self.scenario().to_dict(),
}
registry = IssueRegistry(self.root)
first_ids = registry.ingest(self.report(first), {"s-1": context}, observed_at="2026-07-16T00:00:00Z")
second_ids = registry.ingest(self.report(second), {"s-2": context}, observed_at="2026-07-16T01:00:00Z")
self.assertEqual(first_ids, second_ids)
issue = registry.load(first_ids[0])
self.assertEqual(issue["occurrences"], 2)
self.assertEqual(issue["seeds"], [10, 20])
```

Additional named tests use the same real constructors and assert distinct expectation fingerprints, resolved-issue reopen behavior, 256-entry caps, holdout redaction, invalid identifier/symlink rejection, and preservation of the old index when `os.replace` raises.

Use temporary directories and real JSON reads. Do not mock serialization.

- [ ] **Step 2: Run registry tests and verify RED**

Run:

```powershell
python -m unittest tests.test_issue_registry -v
```

Expected: import failure for `tests.evaluation.issue_registry`.

- [ ] **Step 3: Implement strict helpers and issue fingerprinting**

Implement:

```python
ISSUE_STATUSES = frozenset({"open", "verifying", "resolved"})
ISSUE_ID_PATTERN = re.compile(r"issue-[0-9a-f]{24}\Z")
HISTORY_LIMIT = 256

def issue_fingerprint(
    failure: FailureRecord,
    violation: Violation,
    context: Mapping[str, Any],
) -> str:
    payload = {
        "violation_code": violation.code,
        "minimized_messages": list(failure.minimized_messages),
        "health_bucket": context.get("health_bucket"),
        "intent": context.get("intent"),
        "expectation": context.get("expectation", {}),
        "scenario_hash": context.get("scenario_hash"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
```

Implement `_atomic_write_json`, strict JSON loading, path containment and a lock-file context manager using `os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` with bounded retry and stale-lock cleanup.

- [ ] **Step 4: Implement `IssueRegistry.ingest` and index rebuild**

`IssueRegistry(evaluation_root)` creates `issues/open`, `issues/verifying`, `issues/resolved`, and `issues/index.json`. For every blocking violation in every failure:

- create `issue-{fingerprint}.json` under `open`, or merge into the existing status file;
- increment occurrences;
- update first/last timestamps and commits;
- merge capped history arrays;
- automatically move `verifying` or `resolved` issues back to `open`;
- save non-holdout `scenario` only as `regression_source`;
- save holdout hashes/codes without original or minimized messages.

Return the sorted tuple of touched issue IDs. Rebuild `index.json` from issue files under the lock so the index cannot refer to missing files.

- [ ] **Step 5: Implement validated status transitions**

Add:

```python
registry.set_status(issue_id, "verifying")
registry.set_status(issue_id, "resolved", verification_cycle=cycle_payload)
```

Allow `open -> verifying`, `verifying -> open`, `resolved -> open`; allow `verifying -> resolved` only when cycle status is `completed`, mode is `daily` or `deep`, and the issue ID is absent from `cycle_payload["issue_ids"]`. Reject every other transition.

- [ ] **Step 6: Run registry tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_issue_registry -v
```

Expected: all issue-registry tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/evaluation/issue_registry.py tests/test_issue_registry.py
git commit -m "feat: persist deduplicated evaluation issues"
```

### Task 3: Implement the Resumable Autonomous Cycle

**Files:**
- Create: `tests/evaluation/autonomous_cycle.py`
- Create: `tests/test_autonomous_cycle.py`
- Create: `scripts/run_autonomous_cycle.py`

- [ ] **Step 1: Write failing orchestration tests**

Use fake runner objects returning real `EvaluationReport` instances. Cover deterministic round seeds/directories, completed-round skipping, stop-on-error, continue-on-error, issue ingestion, cycle summary, and CLI exit codes.

```python
result = run_cycle(
    evaluation_root=root,
    cycle_id="quick-20260716-3",
    mode="quick",
    rounds=3,
    base_seed=20260716,
    runner_factory=factory,
)
self.assertEqual(factory.seeds, [20260716, 20260717, 20260718])
self.assertEqual(result["completed_rounds"], 3)
```

Run the same cycle twice and assert the second call does not invoke the factory for completed rounds.

- [ ] **Step 2: Run cycle tests and verify RED**

Run:

```powershell
python -m unittest tests.test_autonomous_cycle -v
```

Expected: import failure for `tests.evaluation.autonomous_cycle` and missing CLI.

- [ ] **Step 3: Implement cycle validation and state persistence**

Define strict cycle IDs with `[A-Za-z0-9._-]{1,80}` and reject reserved Windows stems. Implement `run_cycle` with injectable `runner_factory`, `registry`, `clock`, and UTC timestamp provider. Persist this shape atomically after every state change:

```python
{
    "schema_version": 1,
    "cycle_id": cycle_id,
    "mode": mode,
    "base_seed": base_seed,
    "target_rounds": rounds,
    "status": "running",
    "commit_sha": commit_sha,
    "created_at": created_at,
    "updated_at": updated_at,
    "completed_rounds": 0,
    "issue_ids": [],
    "rounds": [],
}
```

Reject resume attempts whose mode, base seed or target rounds differ from the stored state.

- [ ] **Step 4: Execute rounds and aggregate issues**

For each pending round:

```python
seed = base_seed + round_index
round_dir = cycle_dir / "rounds" / f"{round_index + 1:04d}-{seed}"
runner = runner_factory(round_dir, seed=seed, mode=mode)
report = runner.run_mode()
touched = registry.ingest(
    report,
    runner.scenario_context,
    observed_at=utc_now(),
)
```

Record totals, failures, elapsed time, output path and issue IDs. Evaluation failures are successful rounds with issues; operational exceptions create a failed round. Honor `continue_on_error` only for operational exceptions. Write aggregate `summary.json` and `summary.md` at cycle completion.

- [ ] **Step 5: Implement CLI parsing and exit codes**

Support:

```powershell
python scripts/run_autonomous_cycle.py `
  --mode quick `
  --rounds 10 `
  --seed 20260716 `
  --cycle-id quick-20260716-10 `
  --continue-on-error
```

Defaults: `mode=quick`, `rounds=10`, `seed=20260716`, cycle ID derived from mode/seed/rounds, root `artifacts/evaluation`. Return `0` when all rounds complete without blocking issues, `1` when completed with issues, and `2` for invalid input or operational failure.

- [ ] **Step 6: Run cycle tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_autonomous_cycle -v
```

Expected: all autonomous-cycle tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/evaluation/autonomous_cycle.py tests/test_autonomous_cycle.py scripts/run_autonomous_cycle.py
git commit -m "feat: run resumable autonomous evaluations"
```

### Task 4: Add Issue Management and Regression Export

**Files:**
- Modify: `tests/evaluation/issue_registry.py`
- Modify: `tests/test_issue_registry.py`
- Create: `scripts/manage_evaluation_issue.py`
- Modify: `tests/test_autonomous_cycle.py`

- [ ] **Step 1: Write failing status and export CLI tests**

Cover `open -> verifying`, rejected direct `open -> resolved`, accepted `verifying -> resolved` with a completed daily cycle, rejected quick verification, and export behavior for public/holdout issues.

```python
candidate_path = registry.export_regression_candidate(issue_id)
candidate = Scenario.from_dict(json.loads(candidate_path.read_text(encoding="utf-8")))
self.assertEqual(candidate.messages, tuple(issue["minimized_messages"]))
self.assertTrue(candidate.scenario_id.startswith("regression-"))
```

Assert holdout export raises `ValueError` and writes no file.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_issue_registry tests.test_autonomous_cycle -v
```

Expected: FAIL because export and management CLI are absent.

- [ ] **Step 3: Implement strict regression-candidate export**

Load `regression_source`, replace `scenario_id` with `regression-{fingerprint}`, replace messages with minimized messages, preserve the pre-existing expectation, seed, persona, intent and dialogue mode, then validate through `Scenario.from_dict` before atomic write to:

```text
artifacts/evaluation/candidates/regressions/issue-0123456789abcdef01234567.json
```

Never derive or alter expectation fields from the actual response or violation evidence.

- [ ] **Step 4: Implement the management CLI**

Support exactly one action per invocation:

```powershell
python scripts/manage_evaluation_issue.py issue-0123456789abcdef01234567 --status verifying
python scripts/manage_evaluation_issue.py issue-0123456789abcdef01234567 --status resolved --cycle-id daily-20260716-1
python scripts/manage_evaluation_issue.py issue-0123456789abcdef01234567 --export-regression
```

Load verification cycles only from `artifacts/evaluation/cycles/{cycle_id}/cycle.json`. Print the resulting issue or candidate path. Return `0` on success and `2` on validation or I/O errors.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_issue_registry tests.test_autonomous_cycle -v
```

Expected: all focused tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add tests/evaluation/issue_registry.py tests/test_issue_registry.py scripts/manage_evaluation_issue.py tests/test_autonomous_cycle.py
git commit -m "feat: manage evaluation issue lifecycle"
```

### Task 5: Document, Run and Review Phase 3A

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-15-autonomous-evaluation-issue-registry-design.md` only if implementation requires a clarified invariant; do not weaken requirements.

- [ ] **Step 1: Update operator documentation**

Document quick/daily/deep cycle commands, original storage paths, resume behavior, exit codes, issue states, regression export review requirement, private holdout isolation, and the fact that this is evaluation rather than model-parameter training.

- [ ] **Step 2: Run focused and complete unit tests**

Run:

```powershell
python -m unittest tests.test_issue_registry tests.test_autonomous_cycle tests.test_evaluation_runner -v
python -m unittest discover -s tests -p "test_*.py" -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run a real two-round quick cycle**

Run:

```powershell
python scripts/run_autonomous_cycle.py --mode quick --rounds 2 --seed 20260716 --cycle-id phase3a-smoke-20260716
```

Expected: two round directories, valid cycle/summary JSON, no operational failure, and issue index consistent with blocking results. A nonzero exit code `1` is allowed only when real blocking issues were archived.

- [ ] **Step 4: Run recommendation and repository checks**

Run:

```powershell
python tests/audit_recommendations.py
python tests/nutrition_coverage.py
git diff --check
```

Expected: audit exits `0`, coverage report completes, diff check exits `0`.

- [ ] **Step 5: Request two-stage review**

First review against the design spec, then perform code-quality review focused on path containment, private-data redaction, atomicity, resume corruption and false issue deduplication. Fix every Critical or Important finding and rerun the complete verification.

- [ ] **Step 6: Commit and push**

```powershell
git add README.md
git commit -m "docs: explain autonomous evaluation workflow"
git -c http.proxy= -c https.proxy= push origin feature/nutrition-session
```

Confirm PR #1 remains open with `feature/nutrition-session` as head and `main` as base.

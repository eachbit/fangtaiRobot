# Official Audit Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline 100-point competition-style audit report to every automated evaluation job.

**Architecture:** Keep recommendation logic unchanged. Add rubric aggregation in `app/audit_runner.py`, propagate it through `app/audit_jobs.py`, then render it in the existing web audit console.

**Tech Stack:** Python standard library, existing HTTP server, vanilla JavaScript, existing Playwright smoke test.

---

### Task 1: Rubric Report

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\app\audit_runner.py`
- Test: `D:\Codex\Lesson\fangtaiRobot\tests\test_audit_jobs.py`

- [ ] **Step 1: Write failing tests**

```python
def test_run_audit_includes_official_100_point_report():
    report = run_audit([PASSING_SCENARIO, FAILING_SCENARIO])
    official = report["summary"]["official_report"]
    assert official["max_score"] == 100
    assert 0 <= official["total_score"] <= 100
    assert set(official["sections"]) == {
        "basic_recommendation",
        "complex_scenario",
        "multi_turn_interaction",
        "performance_efficiency",
    }
    assert official["sections"]["basic_recommendation"]["max_score"] == 20
    assert official["sections"]["performance_efficiency"]["max_score"] == 30
    assert official["top_issues"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_audit_jobs.py`

Expected: failure because `official_report` is missing.

- [ ] **Step 3: Implement report builder**

Add `_official_report(records)` and helper functions in `app/audit_runner.py`; call it from `_summary`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_audit_jobs.py`

Expected: `ok: audit runner and background jobs`.

### Task 2: Background Job/API Exposure

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\app\audit_jobs.py`
- Test: `D:\Codex\Lesson\fangtaiRobot\tests\test_server_api.py`

- [ ] **Step 1: Write failing tests**

```python
assert "official_report" in current["summary"]
assert current["summary"]["official_report"]["max_score"] == 100
```

- [ ] **Step 2: Run API test to verify it fails**

Run: `python tests/test_server_api.py`

Expected: failure because background summaries do not expose `official_report`.

- [ ] **Step 3: Reuse audit summary builder in jobs**

Import a public summary helper from `app.audit_runner` or add one, then use it in `AuditJobManager._build_summary`.

- [ ] **Step 4: Run API test to verify it passes**

Run: `python tests/test_server_api.py`

Expected: `ok: audit job HTTP API`.

### Task 3: Web Console Rendering

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\public\app.js`
- Modify: `D:\Codex\Lesson\fangtaiRobot\public\styles.css`
- Test: `D:\Codex\Lesson\fangtaiRobot\tests\web_ui_smoke.py`

- [ ] **Step 1: Write failing smoke assertion**

```python
expect(page.locator("#auditOverview")).to_contain_text("官方评分", timeout=15000)
```

- [ ] **Step 2: Run smoke test to verify it fails**

Run: `python tests/web_ui_smoke.py`

Expected: failure because the text is not rendered.

- [ ] **Step 3: Render report**

Add official score, section scores, and top issue count inside `renderAuditJob`.

- [ ] **Step 4: Run smoke test to verify it passes**

Run: `python tests/web_ui_smoke.py`

Expected: `ok: web audit console`.

### Task 4: Final Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run full checks**

```powershell
python tests/test_agent.py
python tests/test_scenario_agents.py
python tests/test_audit_jobs.py
python tests/test_server_api.py
python tests/web_ui_smoke.py
python tests/test_docker_contract.py
python -m compileall -q app server.py tests
git diff --check
```

- [ ] **Step 2: Commit and push**

```powershell
git add app/audit_runner.py app/audit_jobs.py public/app.js public/styles.css tests/test_audit_jobs.py tests/test_server_api.py tests/web_ui_smoke.py docs/superpowers/plans/2026-07-31-official-audit-report.md
git commit -m "feat: add official audit scoring report"
git -c http.proxy= -c https.proxy= push
```

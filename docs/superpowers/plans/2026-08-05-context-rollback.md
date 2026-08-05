# Context Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic full-history replay and immutable menu-version rollback while preserving the existing recommendation API and offline Docker runtime.

**Architecture:** Extend the in-memory session store with bounded immutable snapshots. Refactor `recommend` into a deterministic turn runner that can replay prefixes, append new turns, or create a rollback version. Expose version metadata through `/api/recommend`, add a read-only history endpoint, and render the current version in the existing audit/demo UI.

**Tech Stack:** Python standard library, existing HTTP server, vanilla JavaScript, existing Playwright smoke tests.

---

### Task 1: Define failing regression tests

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\tests\test_agent.py`
- Modify: `D:\Codex\Lesson\fangtaiRobot\tests\test_server_api.py`

- [ ] **Step 1: Add a test for complete-history replay without a session ID**

Add a test that compares:

```python
history = ["4个人吃午餐，推荐4道菜", "我不吃鸡蛋，其他菜尽量别动"]
replayed = recommend(None, history)

first = recommend(None, [history[0]])
sequential = recommend(None, [history[1]], session_id=first["session_id"])

assert [item["id"] for item in replayed["menu"]] == [item["id"] for item in sequential["menu"]]
assert replayed["changes"] == sequential["changes"]
assert replayed["menu_version"] == 2
```

- [ ] **Step 2: Add tests for explicit and natural-language rollback**

Cover:

```python
first = recommend(None, ["推荐3道晚餐"])
second = recommend(None, ["我不吃虾，其他尽量别动"], session_id=first["session_id"])
restored = recommend(None, [], session_id=second["session_id"], rollback_to=1)
assert restored["changes"]["mode"] == "rollback"
assert restored["changes"]["source_version"] == 1
assert restored["menu_version"] == 3

undone = recommend(None, ["撤销刚才修改"], session_id=second["session_id"])
assert undone["changes"]["mode"] == "rollback"
assert undone["changes"]["source_version"] == 1
```

- [ ] **Step 3: Add a test for rollback followed by a new constraint**

Assert that after rolling back to version 1, adding a new dislike produces a new version and only changes dishes required by that dislike.

- [ ] **Step 4: Add HTTP tests for history and invalid versions**

Assert:

```python
GET /api/sessions/{session_id}/history
```

returns ordered versions, and an invalid `rollback_to` returns HTTP 400 with an explicit error code.

- [ ] **Step 5: Run the focused tests and confirm they fail for missing behavior**

Run:

```powershell
python tests/test_agent.py
python tests/test_server_api.py
```

Expected: failures mention missing `menu_version`, missing rollback behavior, or missing history route.

### Task 2: Implement bounded versioned session storage

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\app\session_store.py`

- [ ] **Step 1: Add immutable snapshot and version fields**

Add `MenuSnapshot` and extend `SessionState` with `menu_version` and `history`.

- [ ] **Step 2: Make `save` append a new version**

Keep the current latest-state fields for compatibility. Each save must append one snapshot, increment the version, preserve at most 32 snapshots, and continue honoring the existing TTL and 256-session limit.

- [ ] **Step 3: Add target lookup, rollback, and history summary methods**

Implement:

```python
store.rollback(session_id: str, target_version: int) -> SessionState
store.history(session_id: str) -> dict
```

Rollback must create a new version copied from the target and retain all prior snapshots.

- [ ] **Step 4: Run the focused tests**

Run:

```powershell
python tests/test_agent.py
```

Expected: storage-related assertions move closer to passing, while agent orchestration assertions may still fail.

### Task 3: Implement replay, rollback parsing, and response metadata

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\app\agent.py`
- Modify: `D:\Codex\Lesson\fangtaiRobot\server.py`

- [ ] **Step 1: Add rollback request parsing**

Recognize explicit `rollback_to` and Chinese phrases for previous-version undo and numeric version targets. Keep reset phrases such as “全部重做” separate from rollback.

- [ ] **Step 2: Refactor recommendation into deterministic turn processing**

Process each new turn from the existing state, using the preceding menu IDs for minimal revision. When no session ID is supplied, process every message prefix and save each resulting snapshot.

- [ ] **Step 3: Implement rollback response construction**

Restore the target snapshot, run current hard-constraint validation against it, and return a new version with `changes.mode == "rollback"`. Rollback must not bypass allergy or dislike checks.

- [ ] **Step 4: Add response metadata**

Return `menu_version` and bounded history summaries from `/api/recommend`.

- [ ] **Step 5: Add the session history GET route and 400 handling**

Add `GET /api/sessions/{session_id}/history` and return a clear 400 response for invalid rollback versions.

- [ ] **Step 6: Run focused tests and the existing agent/API suites**

Run:

```powershell
python tests/test_agent.py
python tests/test_server_api.py
```

Expected: all focused rollback assertions and existing suites pass.

### Task 4: Expose version state in the web demo

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\public\app.js`
- Modify: `D:\Codex\Lesson\fangtaiRobot\public\index.html`
- Modify: `D:\Codex\Lesson\fangtaiRobot\public\styles.css`
- Modify: `D:\Codex\Lesson\fangtaiRobot\tests\web_ui_smoke.py`

- [ ] **Step 1: Add a current-version/history display**

Render the current menu version and a compact list of available versions using existing page sections.

- [ ] **Step 2: Add a rollback control**

Provide a select/button control that calls `/api/recommend` with the current `session_id` and selected `rollback_to`, then refreshes the recommendation view.

- [ ] **Step 3: Add a smoke assertion**

Assert the page contains the version label after a recommendation.

- [ ] **Step 4: Run the web smoke test**

Run:

```powershell
python tests/web_ui_smoke.py
```

Expected: `ok: web audit console`.

### Task 5: Documentation and final verification

**Files:**
- Modify: `D:\Codex\Lesson\fangtaiRobot\README.md`
- Modify: `D:\Codex\Lesson\fangtaiRobot\docs\design\personalized-diet-agent-design.md`
- Modify: `D:\Codex\Lesson\fangtaiRobot\docs\superpowers\specs\2026-08-05-context-rollback-design.md`

- [ ] **Step 1: Document request/response examples**

Document session continuation, complete-history replay, `rollback_to`, natural-language undo, and history endpoint usage.

- [ ] **Step 2: Run the full verification suite**

Run:

```powershell
python tests/test_agent.py
python tests/test_scenario_agents.py
python tests/test_audit_jobs.py
python tests/test_server_api.py
python tests/web_ui_smoke.py
python tests/test_docker_contract.py
python tests/audit_recommendations.py
python -m compileall -q app server.py tests
git diff --check
```

- [ ] **Step 3: Commit the feature**

```powershell
git add app tests public README.md docs
git commit -m "feat: add context replay and menu rollback"
```

- [ ] **Step 4: Push the verified commit to the main branch**

```powershell
git -c http.proxy= -c https.proxy= push origin HEAD:main
```

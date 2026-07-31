# Official Audit Report Design

## Goal

Build an offline audit report that maps every automated recommendation run to the competition's 100-point rubric, so development can focus on the highest-risk scoring gaps before Docker submission.

## Scope

The report is a development and demonstration layer. It must not call external LLM APIs, require network access, or change the `/api/recommend` contract. It should work inside the same Docker image as the official API and web demo.

## Architecture

- `app/audit_runner.py` remains the source of per-scenario evaluation.
- A new report builder will aggregate audit records into four official sections:
  - `basic_recommendation` max 20
  - `complex_scenario` max 20
  - `multi_turn_interaction` max 30
  - `performance_efficiency` max 30
- `app/audit_jobs.py` will include the rubric report in each background job summary.
- `public/app.js` will render the total score, section scores, and top failing examples in the existing audit console.

## Scoring Rules

- Basic recommendation checks hard constraints and official recipe authenticity. Food-forbid violations are penalized as the highest-risk issue.
- Complex scenarios check multi-person/large-table records, menu count mismatch, structure weakness, and nutrition advisories.
- Multi-turn interaction checks session-style scenarios, context preservation, and generated-dialog review quality.
- Performance checks P95 latency, average latency, and timeout-like records using the competition thresholds.

## Outputs

Each audit report returns:

- `total_score`
- `max_score`
- `sections`
- `top_issues`
- `recommendations`

Each section contains a numeric score, max score, metrics, and sample failing record names.

## Testing

Tests must cover:

- Report exists in synchronous `run_audit`.
- Background job summaries expose the same report.
- Agent-generated batches keep hard failures separate from advisory deductions.
- HTTP API returns `official_report`.
- Web smoke test sees the official score panel.

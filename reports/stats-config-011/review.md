# STATS-CONFIG-UI-011 evidence summary

This is a scoped current-schema local Development result, not Production V1 or release acceptance.

## Fixed versions and environment

- Product base: `942777e10d2421db34897761f0dfab895b3464fc`.
- Product implementation: `8a64cb356608174ffbb7153dacbb9a3715c13c5d`.
- Product follow-up fixes: `dee53380a89cbca10783fea0f51ca3950c284e31` and `01785a0a76a3c849f7f4d88fd27fbcb3162929dd`.
- Evidence HEAD: `cb055439c96e2937cb3abe5cbbec13bf47c5d788`.
- Database: `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`.
- Messaging remained isolated at RabbitMQ vhost/queue `property_ai_auth001` / `property_ai.auth001`; this read-only slice did not enqueue a Worker task.
- API 8083 used Development SMS and Fake AI configuration. No real model result is claimed.

`miniprogram/project.config.json` remained unstaged and unchanged at SHA-256 `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. Migration 0010 was not created or executed.

## Implemented user capability

- Tenant administrators can view a current enterprise aggregate or an authorized Property snapshot.
- Property administrators can view only an authorized Property snapshot and cannot escalate to a Tenant aggregate.
- The snapshot shows current work-order state/category/priority counts, Incident total/active counts, SLA clock states and current staffing status using one generated instant and each Property's timezone/business date.
- Resident, Staff, Supervisor and SYSTEM roles do not receive `stats:read` by default.
- The page checks returned scope, suppresses out-of-order responses, clears a stale Property snapshot while changing a Property target, and preserves an enterprise aggregate while only changing the configuration target.
- Configuration cards navigate to the existing staff, SLA, property and knowledge pages under their existing permissions; the overview does not add a second save path or expose deployment/provider secrets.

## Tests and runtime evidence

- Focused unit tests: `11 passed, 1 warning`.
- Pure unit regression: `539 passed, 1 warning` after a controlled repository-local temporary-directory rerun; the first attempt had only Windows temporary-directory permission errors.
- Runtime API and read-only database evidence use normal Development SMS sessions and match tenant 1/property 1: six ASSIGNED/ELECTRICAL/HIGH work orders, one OPEN Incident, six running OPEN SLA clocks, one current assignment and zero current daily-status rows.
- Negative API evidence covers Property Admin tenant denial, Resident/Staff denial, cross-tenant denial and invalid Tenant-plus-Property query shape.
- Actual Developer Tools evidence covers Tenant Admin Home entry and both scopes, all four existing configuration links, same-target SLA navigation, Property Admin property-only behavior, Resident/Staff entry exclusion and a same-scope reload.
- The main implementation session opened and visually inspected all nine screenshots. The independent reviewer separately opened all nine.

## Independent review

`reports/codex/STATS-CONFIG-UI-011-review.json` is round-1 `PASS` at evidence HEAD `cb055439c96e2937cb3abe5cbbec13bf47c5d788`, with no `CURRENT_TASK_BLOCKER`.

- `STATS011-N1` is closed by updating the living handoff and current effective matrix view. The original 83-item baseline row remains historical and points to the current overlay rather than being silently rewritten.
- `STATS011-R1` retains production-volume/performance, production RBAC, real-device/provider/model and formal release validation.
- `STATS011-R2` retains historical KPI period/denominator semantics, stored reports/exports and production deployment/provider configuration. These semantics were not invented in this slice.

No real Qwen/bge-m3 result, production provider operation, Production V1 acceptance, account merge, or Migration 0010 is claimed.

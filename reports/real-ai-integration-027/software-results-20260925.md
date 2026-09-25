# Current software checks

- Low-confidence clarification implementation and directly affected work-order/dispatch/page tests: **120 passed, 3 warnings**. The first run had 118 passes and two failures caused by two older fixtures not installing the 025 execution-lease test double; only fixture isolation was corrected before the passing run.
- Backup/restore target-guard tests: **11 passed, 3 warnings**.
- Combined low-confidence plus restore guard selection: **61 passed, 3 warnings**.
- New Incident disposition and page-wiring selection: first run **13 passed, 1 failed, 4 warnings** because the static assertion did not allow the deliberate line break before `updateIncidentStatus`; after correcting only that test matcher and disabling pytest cache writes, **14 passed, 1 warning**.

These are scoped unit/wiring checks. They do not constitute page/API/database runtime acceptance for Incident disposition, a real-model quality pass, a real AUTO pass, or Production V1 release acceptance.

## Incremental finding-fix verification

- After the round-1 review fixes, the directly affected analysis-contract, work-order presentation and Incident assertions reported **61 passed, 1 warning**.
- Four restore-guard cases in that combined invocation did not reach their assertions because pytest could not access its user-profile temporary directory. Running the complete restore-guard file with a fresh workspace-local temporary base then reported **12 passed, 1 warning**.
- The exact scope and the missing-raw-output boundary are recorded in `tests-incremental-fix-round1.json`. These results do not add database, page, model or production evidence.
- After adding the explicit service-boundary `INVALID_OUTPUT` assertion for unknown category values, that analysis-contract file reported **17 passed, 1 warning**; the scoped addendum is `tests-incremental-fix-round1-addendum.json`.

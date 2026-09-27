# 027 structured contract / pending 0014 batch evidence

- Baseline HEAD: `1847dae9b3d3c7323ae80ab9fa8ca33382f4ee78`.
- Work-order LLM requests now carry the strict JSON Schema generated from the
  same `ObservedAnalysisOutput` used by server validation. Unknown enum values
  are rejected rather than silently mapped to OTHER.
- Fixed diagnostic used 31 Qwen requests and zero ASR requests. Requests 1–30
  returned structurally valid results; request 31 returned an invalid nested
  VLM JSON wrapper and stopped the collector. Request 32 and real AUTO were not
  run. Exact semantic/provider failures and raw hashes are in
  `diagnostic-r3schema/result-summary.json`; status remains `NOT_READY`.
- Focused identity/SLA/AI/audit verification passed 125 tests with one existing
  dependency deprecation warning. After integration repairs, the complete pure
  unit suite passed **1075 tests** with the same one warning. The final
  identity/SLA/migration subset passed 21 tests, and the direct metadata
  fail-closed regression passed 8 tests. These are main-session observed test
  results; the read-only reviewer did not rerun them.
- Mini-program JavaScript syntax and `app.json` parsing passed. No Developer
  Tools/page claim is made for the pending identity/SLA UI in this batch.
- Proposed 0014 was compiled/imported and statically checked only. It was not
  executed against AUTH001 or any other database.
- Independent incremental read-only review: **PASS_SCOPED**, with four findings
  closed in the same round. It does not approve 0014 execution, identity
  completion, R3, real AUTO or Production. Saved at
  `reports/codex/REAL-AI-INTEGRATION-027-PENDING0014-review.json`; the complete
  original reviewer payload is preserved alongside it as
  `REAL-AI-INTEGRATION-027-PENDING0014-review.raw.json`.

# KNOWLEDGE-MGMT-RAG-008 runtime and page evidence

Task scope: controlled Python 3.12 recovery plus the already implemented knowledge-management/RAG slice. This is local isolated DEVELOPMENT acceptance only. It is not real bge-m3/Qwen, Production V1, Day 4, or release acceptance.

## Version and runtime

- Knowledge source baseline: `bfbf436615b7875ca1ee24527ee335bfe21522e3`.
- Runtime defect fix: `966142bfeac72c6e4ed6a34d440b9464bf859fd2` (`fix: ground development rag answers in cited evidence`).
- Python: `D:\property-ai\tmp\uv-python\cpython-3.12.10-windows-x86_64-none\python.exe`, CPython 3.12.10, AMD64, 64-bit. Executable SHA-256: `6461FE8DC13C642302F591C0C1C16B220629F7A336F74CE88AA2CDD31C43E62A`.
- uv: `0.11.24 (5e04460c0 2026-06-23 x86_64-pc-windows-msvc)`. It installed build `20250529` from the official `astral-sh/python-build-standalone` GitHub release after the primary Astral endpoint fallback. `--no-bin --no-registry` was used with process-scoped `UV_PYTHON_INSTALL_DIR` and `UV_CACHE_DIR`; no global PATH, registry, file association, default interpreter, or old `.venv` file was changed.
- Dependency reuse: isolated Python `-I`, `PYTHONNOUSERSITE=1`, with only `D:\property-ai\.venv\Lib\site-packages` explicitly added. Actual package versions and loaded paths are recorded in `runtime-final-snapshot.json`; user site is disabled. The missing old base interpreter remains an observed fact; its disappearance cause is `UNKNOWN`.
- The interpreter and uv cache remain under `tmp/` and are intentionally retained but Git-ignored. They are not a durable production runtime arrangement.
- Current isolated API PID at evidence time: `8360`; Worker launcher/Celery PIDs: `35748` / `45280`. All used the recovered interpreter and code `966142b`. Logs: `tmp/auth001-runtime/api-966142b-knowledge.stdout.log` and `tmp/auth001-runtime/worker-966142b-knowledge.stdout.log`.
- Runtime target: API `http://127.0.0.1:8083/api/v1`; MySQL `127.0.0.1:3307/property_ai_auth001_test` at `20260919_0009`; RabbitMQ vhost `property_ai_auth001`, queue `property_ai.auth001`; Milvus collection `knowledge_chunks_auth001`, DEVELOPMENT vector dimension 8. Port 8082 and shared services were not replaced or rebuilt.

## Tests

- Collection-only check: 19 knowledge tests collected and pytest exited normally.
- Initial focused knowledge tests: 19 passed.
- Initial pure-unit run: the first attempt had two sandbox temp-path errors; a new workspace-local `--basetemp` rerun passed 489 tests with 1 warning.
- After the grounded Development-RAG fix: focused knowledge tests `20 passed, 1 warning`; full pure-unit regression `490 passed, 1 warning`.
- The broad integration fixture was deliberately not executed: inspection showed cross-table DELETE operations, and the migration test performs downgrade/upgrade. Both are outside this task's authorization.

## Business and page evidence

All business state transitions used authenticated page actions or business APIs. SQL was read-only evidence collection. Test accounts and documents are synthetic; tokens, phone numbers, passwords, and OTPs are not stored in the evidence.

1. A PROPERTY_ADMIN used the actual maintenance page to create document `1` (`KB008-20260920-1933`) in property/tenant `4`. The isolated Worker ingested v1, and the page showed `ACTIVE` / `SUCCEEDED`.
2. With only the isolated Worker temporarily configured for a Development embedding failure, valid v2 became `FAILED / KB.UNAVAILABLE`; v1 remained the current active version. The normal Worker was restored before continuing.
3. The page submitted v3. The Worker activated it; the page showed v3 `ACTIVE/SUCCEEDED`, v2 `FAILED`, v1 `SUPERSEDED`.
4. A resident logged in normally and queried through the actual assistant page. Retrieval cited document `1`, version `3`, with the expected locator and excerpt.
5. That first query exposed an actual defect: the Development LLM branch returned an unrelated hard-coded answer despite the correct citation. Commit `966142b` now builds the Development answer from the selected evidence excerpt and has a focused regression test. The repeated page query returned the v3 excerpt and the same v3 citation.
6. API negatives separately proved resident create/view/archive `403 IAM.FORBIDDEN`, claimed-tenant mismatch `422 KB.SCOPE_MISMATCH`, and missing property `404 KB.NOT_FOUND`.
7. A synthetic `STAFF_ONLY` document was ingested by the Worker. It was absent from resident citations and present for the manager, then archived through the business API.
8. Concurrent version requests on auxiliary document `3` allocated distinct versions 2 and 3. Two repeat tasks were received by the isolated Worker; ingestion attempt stayed `2 -> 2` and MySQL chunks stayed `1 -> 1`. The auxiliary document was then archived and disappeared from resident retrieval.
9. The PROPERTY_ADMIN selected document `1` and clicked the actual page archive control. The page returned `文档 #1 已归档`. The archived view retained v1/v2/v3 and all ingestion outcomes; the screenshot was visually inspected.
10. The resident logged in again through the real page and repeated the query. The assistant page returned `insufficient_knowledge` and zero citations; its screenshot was visually inspected.

Evidence:

- `runtime-negative-concurrency-evidence.json`: API negative, visibility, concurrency, idempotency, MySQL/Milvus and audit evidence.
- `runtime-final-snapshot.json`: final read-only DB/audit/Milvus state, interpreter/dependency paths, test summary and screenshot hashes.
- `01-create-form.png` through `09-resident-query-after-archive.png`: actual Developer Tools page evidence. `06` deliberately preserves the pre-fix mismatch; `07` is the corrected grounded query; `08` is archived history; `09` is the no-citation query after archive.
- The mixed-role two-tenant branch had no suitable runtime identity in this isolated dataset. Its focused unit coverage remains the evidence for that exact branch; no role or second tenant was fabricated.

## Boundaries and remaining gaps

- Development SMS inbox, DEVELOPMENT embedding (8 dimensions), and Development LLM were used. This proves software wiring, scope enforcement and task processing, not model quality.
- Real bge-m3 embeddings, self-hosted Qwen answer quality, reranking quality, production collection sizing/indexing, production Worker supervision, production object/storage/security, and formal release remain unverified release blockers.
- The final read-only snapshot shows Milvus rows retained while the MySQL document is archived. Normal retrieval correctly excludes the archived document; physical vector retention/deletion policy was not changed in this task.
- No migration, dependency install/upgrade, shared-vhost consume/replay/clear, shared service rebuild, GPU rental, model download, paid call, push, reset, restore, or `0010` operation occurred.
- Independent runtime-evidence review is the final gate for this scoped task; the prior FIX-2 review remains static-code evidence only.

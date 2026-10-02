# CODEBASE-REMEDIATION-20261003

## Scope and baseline

- Baseline B: `53b7bb0d20bee32326ee71de3a29d68916fc0927`.
- Inspected backend, native Mini Program, tests, configuration, operations scripts and delivery entry points against the frozen Python 3.12 / FastAPI / synchronous SQLAlchemy / native WeChat stack.
- Protected pre-existing changes remained byte-for-byte unchanged:
  - `backend/app/model/ai.py`: `FFC6419C0C1FBFB71C19E5542E71C07BCE23C3E67D61264144CB1C81AE3E0F8E`
  - `miniprogram/project.config.json`: `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`
- Recoverable source backup: `tmp/codebase-remediation-baseline-B-20261003-022811/` (not a delivery artifact and not for cleanup in this task).

## Remediation completed

- Production now rejects debug mode and does not expose OpenAPI/Swagger/ReDoc.
- File API responses no longer expose storage/scanner internals or presigned object paths. Upload reads are bounded to the configured limit plus one byte before rejection.
- Loopback, private IP, localhost and single-label Compose provider targets ignore environment HTTP proxies. External providers retain explicit proxy support.
- Knowledge-management listing batches latest-version and latest-job reads rather than issuing two queries per document.
- Identity-recovery and employee-invitation public errors no longer expose schema/provider/internal-ID implementation detail.
- Mini Program ordinary surfaces now use business labels and safe error copy. Login/home no longer render development instructions. Property selection on announcements, attendance, binding review and dispatch uses the authorized property catalog instead of numeric ID input.
- Deep-linked queue items receive the same status/reason projection as listed items. Missing employee display names fall back to the documented employee number.
- Shared styles cover form controls, feedback, touch targets, safe-area spacing and narrow-screen layout. Selected-media preview and native supplement/receipt behavior remain intact.
- No high-confidence unreferenced product source was deleted. Historical evidence, runtime assets and recovery data were not treated as dead code.

## Verification

- Pure unit candidate: `1295 passed, 1 warning` (`Starlette` dependency deprecation only), Python 3.12.10 with the retained isolated dependency reuse.
- Focused remediation and page-control regression: `69 passed, 1 warning`.
- All Mini Program JavaScript files passed `node --check`; four Mini Program test scripts passed.
- Python `compileall` passed; `git diff --check` passed.
- WeChat Developer Tools compiled all changed WXML files and `app.wxss` successfully.
- Actual simulator login page was opened and inspected. Before: `reports/mp-local-vis-001/03-login-page.png`; after: `reports/codebase-remediation-20261003/ui/after-login-clean.png`.
- Independent Standards review closed S-01 through S-04 after the direct corrections. Independent Spec review closed the storage response issue and requested removal of the final home environment note; that final removal is included here for the last incremental confirmation.

## Runtime and remaining boundaries

- Product source is committed as `a14f5db143940ab3427f7ae5d4381e9cc95906ac`. The online 8093 API/Worker/publisher/Beat all run `property-ai-delivery:a14f5db` / `sha256:eb6f12cd6f20b0542b21b85ab4612014b27555c20a406159ff589d8c31c53949`, built offline from the pure Git archive over the retained dependency image. Health/readiness pass, the running `app/main.py` hash matches the archive, and the dedicated queue is `0 ready / 0 unacknowledged / 1 consumer`; historical DLQ messages were untouched.
- Current-schema supplement and BEFORE/AFTER receipt implementations remain compiled and unit-tested. Existing native WO19 media evidence is preserved and was not replayed.
- Authoritative reopened-cycle media association is not representable in the current schema: `work_order_media` lacks `process_id` / `receipt_id`. The minimal next migration must be separately approved before creation or execution; no timestamp/audit-JSON workaround was introduced.
- Current-device native supplement submission and current-device BEFORE/AFTER receipt submission remain pending. They require one coordinated device session after a fixed remediated runtime is loaded; API/mock submission is not an acceptable substitute.
- Real SMS/WeChat providers, trusted identity proof, malware scanning, target host/domain/TLS, production-scale security/performance/durability, media-byte disaster recovery and formal release remain external release gates, not local software PASS.

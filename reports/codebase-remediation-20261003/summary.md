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

## Full Mini Program usability pass (2026-10-03)

### Coverage record

- Frontend baseline for this pass: `b5f88353a8868a13d9650c23e22d8d16c6b5a3c5`.
- Registration inventory: 29 main-package pages, no subpackages, 30 WXML templates, 16 WXSS files and one custom component (`work-order-contact`).
- All 29 registered routes were included in 40 role-specific open checks: unauthenticated 3; resident 11; staff 10; manager 16. The route groups covered login/reset/debug login; resident home/profile/recovery/work orders/houses/announcements/assistant/notifications; employee claim/attendance/tasks; and manager queue/incidents/announcements/bindings/staff/properties/knowledge/SLA/dashboard/recovery/organization creation.
- The audit used normal password authentication for the three authenticated roles. It created normal authentication sessions but did not submit a form that creates or changes a business object, send an SMS, create a work order or call a model.
- Raw coverage is in `ui/role-page-coverage.json`. All 40 checks returned the requested `currentPage`, none was `UNKNOWN`, and no unexpected redirect occurred. Every role-route check has a 299x659 simulator screenshot. Password reset was reached through the actual “忘记密码” control rather than by inferring a direct route.
- Query-specific resident/staff work-order detail checks exercised the rejection/error layout with an object not owned by the selected fixture identity; they do not count as positive detail-page authorization evidence. The manager announcement and identity-review pages similarly demonstrate safe permission denial for the available manager fixture, not their positive authorized flows.
- Focused state evidence is in `ui/ui-state-evidence.json`: a manager-selected work order renders the shared contact component with a masked number and both contact actions; a local UI-only long error state wraps at 299x659 while login actions are disabled; and the password control accepted a real focus tap. These actions created no business object and made no model/SMS call.
- Conditional-layout evidence is in `ui/conditional-layout-evidence.json`. UI-only synthetic page state rendered the announcement draft form, an identity-review card, an active resident relation and the resident cancel region; both the native relationship-end and work-order-cancel confirmation modals were then actually opened and captured without selecting confirm. This proves conditional layout and native-modal rendering only, not authorization or transaction behavior.

### Common causes and corrections

- The shared form selector gave `input`, `textarea` and picker-like controls the same vertical padding without a single-line input height. Native input text and placeholders could therefore be clipped. The shared stylesheet now gives single-line inputs an 88rpx fixed box, textareas a separate multiline minimum and line height, and picker/view fields their own vertically centered minimum.
- Generic page-level `input` and `.picker` rules in assistant and management pages overrode the common box model. Those duplicate overrides were removed; page files retain only genuinely page-specific textarea/source/CSV sizing.
- Native buttons used inconsistent aliases and inherited native sizing differently inside and outside cards. Main, secondary, compact, inline/action-row and link controls now have distinct visual sizing, centered content and an 88rpx minimum touch height; compact controls remain distinct through padding, typography and margins rather than a smaller hit area.
- The work-order contact component was style-isolated and its native buttons had no product button class. It now intentionally consumes the shared style contract and uses explicit primary/secondary/error presentation.
- Safe-area spacing, card box sizing, password-row shrink behavior, wrapped switch/action rows, nested feedback spacing and the narrow-screen rule were hardened without hiding overflow or shrinking text to conceal clipping.

### Representative visual evidence

| Area | Before | After |
| --- | --- | --- |
| Attendance input/action sizing | `ui/before-attendance-full-audit.png` | `ui/after-attendance-full-audit.png` |
| Staff profile/password controls | `ui/before-profile-staff.png` | `ui/after-profile-staff-full-audit.png` |
| Assistant query form | `ui/before-assistant-staff.png` | `ui/after-assistant-staff-full-audit.png` |
| Staff work-order list actions | `ui/before-staff-orders-list.png` | `ui/after-staff-orders-list-full-audit.png` |

Additional rendered checks cover all 40 unauthenticated/resident/staff/manager route combinations under `ui/audit-*.png`. `ui/audit-manager-queue-contact.png` shows the positive shared contact component, and `ui/audit-common-long-error-disabled.png` shows the forced long-error/disabled state. These screenshots establish the inspected simulator layouts, not universal device compatibility.

### Verification boundary

- The concise command/result log is `ui/frontend-verification.txt`; route/state metadata are `ui/role-page-coverage.json` and `ui/ui-state-evidence.json`.
- All Mini Program JavaScript files passed `node --check`.
- Five Mini Program test scripts passed. The UI test parses individual CSS rule bodies (rather than matching across unrelated rules), covers all 29 registered pages and every native button in page/component templates, and asserts the contact component's intentional shared-style contract.
- All changed WXML and WXSS files compiled in WeChat Developer Tools. One intermediate batch hit the tool's 60-calls-per-minute limit; the bounded retry after the window passed compiled every remaining changed stylesheet successfully.
- Confirmed visually at 299x659: normal/disabled buttons, single-line inputs, multiline fields, pickers, long lists/cards, inline actions, wrapped long errors, empty states, permission errors, password-reset rendering and the positive masked-contact component.
- Conditional manager forms and both native confirmation modals were visually checked through explicitly marked UI-only synthetic state; no confirm action or business request was sent. Positive authorized announcement/identity transaction flows remain outside this visual evidence because the available manager fixture lacks those permissions.
- The password input focus tap succeeded, but the automator screenshot does not expose the soft keyboard. Not claimed in this pass: keyboard height/dismissal and IME variants, enlarged OS font, or notched-device/physical-device safe-area behavior. These are explicit device-bound follow-ups rather than inferred PASS results.
- The existing independent read-only Spec reviewer returned Round 3 `PASS`: all prior frontend findings are closed, the UI-only/no-confirm evidence boundary is accurate, and the remaining device-dependent items stay explicitly `UNVERIFIED`. The saved review is `ui/independent-spec-review-round3.txt`.

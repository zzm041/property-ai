You are the single independent read-only Codex reviewer for `D:\property-ai`.
Do not edit files, start/stop processes, run tests, operate pages, send HTTP or
business requests, inspect secrets/private local configuration, or create another
reviewer. Return the configured structured JSON schema.

## Fixed review identity

- Task: `ANNOUNCEMENT-GOVERNANCE-007`, final bounded review round 3 of 3.
- Existing 0009 schema only; no migration/dependency changes.
- Prior fix commit `90752f3694364e27fe212a43f31462bd82137ecb` closed
  `ANN007-B1` and `ANN007-B2` in your prior review.
- Page-scope fix commit `09e2253c176257ae03f32ed4338627835bbaccda` addresses
  the sole remaining current-task blocker `ANN007-B3`.
- Verify actual HEAD. Do not review unrelated operator
  `miniprogram/project.config.json`, pre-existing `backend/app/model/ai.py`
  line-ending dirt, or unrelated evidence noise as task source.
- 0010/account merge is separate and remains unapproved.

## Re-review scope

Review only `ANN007-B3` and direct regression from
`90752f3694364e27fe212a43f31462bd82137ecb..09e2253c176257ae03f32ed4338627835bbaccda`.
Do not repeat the full announcement or repository audit. Keep B1, B2 and C1 closed
absent new contrary evidence.

Prior B3 trigger: a tenant administrator opens a propertyId-only route, or mixed
grants include tenant A plus property B while the route supplies tenantId=A. The
page must derive tenant identity from the selected property returned by the
permission-filtered property catalog, not trust route tenantId. Capability checks
must use the matching grant for that verified tenant/property; selected TENANT
targets must use tenant publish permission. Server authorization remains final.

Inspect:

- `miniprogram/pages/supervisor/announcements.js`
- `miniprogram/services/api.js` and property-catalog call context as needed
- `miniprogram/pages/home/home.js` only for route context
- `backend/app/domain/iam.py` only for scope-semantics comparison
- the exact commit diff above

Main-session checks, not reviewer reruns: Node syntax, `git diff --check`, pure Node
scope-helper cases, and focused announcement unit tests `9 passed`.

## Saved evidence

- `reports/announcement-governance-007/page-scope-helper-evidence.json`
  proves pure mixed-grant cases without API/business writes.
- `reports/announcement-governance-007/page-scope-fix-evidence.json`
  records actual 9435 page automation through official DEVELOPMENT SMS login:
  propertyId-only tenant-admin route; selected tenant target; deliberately
  mismatched route tenant ignored; property-admin list excludes tenant target;
  no business object created.
- `reports/announcement-governance-007/04-property-id-only-tenant.png`
- `reports/announcement-governance-007/05-mismatched-route-property.png`

Actually open both PNGs with supported image inspection. The first should show the
TENANT-target announcement #10 selected and publish history; the second should show
the property-admin form/list without #10. Check that no resident private data is
visible. Existing `page-evidence.json` and runtime-fix1 evidence remain the accepted
B1/B2/C1 evidence; reread only if a direct contradiction appears.

The backend was unchanged by this page-only commit and remains the prior 90752f3
runtime on 8083/0009; 8082 and isolated Worker were unchanged. This is local
DEVELOPMENT SMS, no AI, not production or whole Production V1 acceptance.

## Result rules

Use stable finding IDs, with category, exact location, trigger, impact and evidence.
PASS only if B3 is closed and no directly introduced current-task blocker exists.
Keep `ANN007-N1` cross-property runtime coverage as NON_BLOCKING and `ANN007-R1`
shared RabbitMQ replay as RELEASE_BLOCKER; neither blocks this local slice.
State the reviewer's actual read-only boundary and inspected source/evidence/images.

`next_action` must be one concrete main-Codex operation after this slice. Approval
requests include only genuine user decisions. Do not hand work to Cursor.

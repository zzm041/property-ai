# Project handoff (latest facts only)

Updated for Cursor + Codex CLI collaboration. Not a full-repo audit.

| Item | Current fact |
| --- | --- |
| Repo | `D:\property-ai` |
| HEAD | `67fe245adda3fe9424ff10d401667abfa53b56c7` — VIS-001 evidence / Codex PASS. Product login wiring still `c44c051` |
| Working tree | one pre-existing unstaged file: `miniprogram/project.config.json` |
| That file SHA-256 | current `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F` (was `3FBE70AE…` before DevTools rewrite of trailing newline). Diff vs HEAD is still only operator AppID / compile settings |
| That file meaning | operator replaced AppID (`wxcfa5fd7dc03cac56`) and DevTools compile settings. Keep as-is. Do not stage, revert, or overwrite unless newly changed |
| Implementer pytest history | 577 passed, 2 skipped (Day 4 focused-fix). Historical only; not this-task evidence |
| Do not reuse | old 547-passed counts; learning checkpoint `5e9e8ba` as current progress |
| Local API | `http://127.0.0.1:8082/api/v1` (Day 4 Nginx). 2026-09-19 probe: `/healthz` 200, `/readyz` 200 ready |
| Mini program import | `D:\property-ai\miniprogram` |
| Local preview login | `POST /auth/dev/token` with `seed_key` (develop + `env.local.js` `localPreview`). Resident seed `__DEV_RESIDENT__` |
| DevTools | installed. CLI `D:\微信web开发者工具\cli.bat`. `islogin` failed: IDE service port **off** |
| Codex CLI | `codex.cmd` 0.155.1, ChatGPT login. Reviewer only |
| Current task | `MP-LOCAL-BATCH-002` complete: VIS-001/002/003/004 PASS. |
| Codex plan | session `01a0b812-28ee-79e0-a462-408409775d3a`; first schema call failed (`session_note` required); second call exit 0, decision `WAIT_HUMAN`. Files: `reports/codex/MP-LOCAL-VIS-001-plan.json`, `reports/codex/MP-LOCAL-VIS-001-plan.call.json` |
| Codex review | VIS-001 PASS `01a0b820-bf4a-7692-82b7-bd8de913aed2`. VIS-002 PASS `01a0b842-9d6c-7b71-a9ef-739cdda92a94`. VIS-003 PASS `01a0b847-851d-76b3-94c4-07e98e369012`. VIS-004 PASS `01a0b84b-13db-7873-b474-d8ed3d0326ce`. |
| This-task evidence | Batch objects: house 2 room 102, bind request 11 / relation 11, work order 651 CLOSED, announcement 1, notification 54 read, assistant citations document_id 3 and 4. See `reports/mp-local-batch-002/`. |
| Out of this task | house bind, work-order chain, official WeChat login, upload/release |
| Still open release blockers (not this-task gates) | Migration 0008, CVE pin upgrades, dataplane `0.0.0.0`, live vendor credentials, Day 4 not production PASS |

`env.local.js` and `project.private.config.json` stay gitignored. Do not export secrets, tokens, or `.env`.

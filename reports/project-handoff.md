# Project handoff (latest facts only)

Updated for Cursor + Codex CLI collaboration. Not a full-repo audit.

| Item | Current fact |
| --- | --- |
| Repo | `D:\property-ai` |
| HEAD | `7c847813f02d22581aabbb476a2dd5667aa66d47` — collaboration evidence commit; product login wiring still `c44c051` |
| Working tree | one pre-existing unstaged file: `miniprogram/project.config.json` |
| That file SHA-256 | `3FBE70AE8438D137498E55BDD289357473715231E4C96DABA7DEF7601741352D` |
| That file meaning | operator replaced AppID (`wxcfa5fd7dc03cac56`) and DevTools compile settings. Keep as-is. Do not stage, revert, or overwrite unless newly changed |
| Implementer pytest history | 577 passed, 2 skipped (Day 4 focused-fix). Historical only; not this-task evidence |
| Do not reuse | old 547-passed counts; learning checkpoint `5e9e8ba` as current progress |
| Local API | `http://127.0.0.1:8082/api/v1` (Day 4 Nginx). 2026-09-19 probe: `/healthz` 200, `/readyz` 200 ready |
| Mini program import | `D:\property-ai\miniprogram` |
| Local preview login | `POST /auth/dev/token` with `seed_key` (develop + `env.local.js` `localPreview`). Resident seed `__DEV_RESIDENT__` |
| DevTools | installed. CLI `D:\微信web开发者工具\cli.bat`. `islogin` failed: IDE service port **off** |
| Codex CLI | `codex.cmd` 0.155.1, ChatGPT login. Reviewer only |
| Current task | `MP-LOCAL-VIS-001`: compile + show login + tap 以住户登录 + `/auth/dev/token` and `/me` succeed + resident home from real `/me` |
| Codex plan | session `01a0b812-28ee-79e0-a462-408409775d3a`; first schema call failed (`session_note` required); second call exit 0, decision `WAIT_HUMAN`. Files: `reports/codex/MP-LOCAL-VIS-001-plan.json`, `reports/codex/MP-LOCAL-VIS-001-plan.call.json` |
| Codex review | first `01a0b819-0019-78b1-b8ff-c8ec11ca1897` `WAIT_HUMAN`; after port+UI, review2 first call failed (model capacity, not forged); retry session `01a0b820-bf4a-7692-82b7-bd8de913aed2` exit 0 decision `PASS`. File: `reports/codex/MP-LOCAL-VIS-001-review2.json` |
| This-task evidence | Service port now on (`127.0.0.1:24540`, `islogin=true`). `cli open`/`cli auto` ok. Login page screenshot + tap 以住户登录. Live POST `/auth/dev/token` 200 and GET `/me` 200. Home shows `__DEV_RESIDENT__` / `RESIDENT` from `/me`. See `reports/mp-local-vis-001/` |
| Out of this task | house bind, work-order chain, official WeChat login, upload/release |
| Still open release blockers (not this-task gates) | Migration 0008, CVE pin upgrades, dataplane `0.0.0.0`, live vendor credentials, Day 4 not production PASS |

`env.local.js` and `project.private.config.json` stay gitignored. Do not export secrets, tokens, or `.env`.

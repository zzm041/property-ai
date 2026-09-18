# Local miniprogram preview (limited Day 5 UI)

Not Day 4 production PASS. Not enterprise delivery. Not a claim that WeChat DevTools compiled or displayed pages.

Status labels: PASS / FAIL / PARTIAL / NOT_RUN / BLOCKED / NEED_OPERATOR.

---

## 1. Baseline

| Item | Actual |
| --- | --- |
| Repo | `D:\property-ai` (not `D:\property-ai-recovery\…`) |
| Start HEAD | `7608f930a46fe06069b40db44994fcf533ee94df` |
| Interpreter | `d:\property-ai\.venv\Scripts\python.exe` |
| Day 4 origin | `http://127.0.0.1:8082` → `api-day4` |
| Day 4 DB | `property_ai_day4` on `:3307`, revision `20260917_0007` |
| API image after this round | `property-ai-api:latest` `sha256:4ee723b15d33947a2334dd9f37a5997e7772fbf39efefe463c9829ea64c7faf8` |
| Recreated | `api-day4` only. mysql / mysql-test / Redis / RabbitMQ / MinIO / Milvus **not** recreated |
| External AI | Fake / Development providers. Banner on login/home |
| `env.local.js` / `project.private.config.json` | local only, gitignored |

---

## 2. WeChat DevTools

**Not installed** on this machine.

- Winget package exists: `Tencent.WeixinDevTools` (listed 2.02.2608070)
- `winget list --id Tencent.WeixinDevTools` → no installed package
- Start Menu only has the WeChat messenger (`D:\Weixin`), not 微信开发者工具
- No `cli.bat`

Therefore: **pages were not compiled or opened in DevTools**. Login/home display = **NEED_OPERATOR**. Do not treat this report as UI verification.

Install (operator):

1. `winget install --id Tencent.WeixinDevTools -e`
2. Open 微信开发者工具, sign in with **your** WeChat (I cannot complete that QR scan)
3. Import directory (see §3)
4. Use tourist AppID already in `project.config.json` (`touristappid`). Do not invent a new AppID
5. Confirm local settings: Details → local settings → **do not verify valid domain / web-view / TLS** (also set in gitignored `miniprogram/project.private.config.json` as `urlCheck: false`)

---

## 3. Import directory and API

Import **exactly**:

`D:\property-ai\miniprogram`

`miniprogramRoot` is `./` inside that folder. Do not import the repo root. Do not import the recovery worktree.

Local API (already running, probed this round):

| Check | Result |
| --- | --- |
| `GET http://127.0.0.1:8082/healthz` | 200 `status=ok` |
| `GET http://127.0.0.1:8082/api/v1/readyz` | 200 `ready` |
| Mini program `BASE_URL` | `http://127.0.0.1:8082/api/v1` via gitignored `config/env.local.js` `develop` |
| `:8000` on host | not published (container-internal) |
| `:8080` development Nginx | up, not used for this preview |

Copy if missing: `miniprogram/config/env.local.example.js` → `env.local.js`. Current machine already has `env.local.js` with `localPreview: true` and Day 4 URL.

---

## 4. AppID / login honesty

| Item | Status |
| --- | --- |
| `project.config.json` `appid` | `touristappid` (existing; not invented) |
| Official WeChat `wx.login` | **NOT_RUN** / expected to fail in tourist mode without certified AppID and `WECHAT_APP_SECRET` |
| Local preview login | API **PASS** via `POST /api/v1/auth/dev/token` `{ "seed_key": "…" }` (DevAuth Guard still on; staging/prod compose remain `DEV_AUTH_ENABLED=false`) |
| UI button “以住户/物业/员工登录” | code exists; **NEED_OPERATOR** to see |

`/me` after seed login (HTTP, tokens not logged):

| Seed key | roles | notes |
| --- | --- | --- |
| `__DEV_RESIDENT__` | `RESIDENT` | `work_order:create`; **no house relation yet** |
| `__DEV_PROPERTY_MANAGER__` | `PROPERTY_ADMIN` | `work_order:dispatch` |
| `__DEV_STAFF__` | `STAFF` | `work_order:process`; employee_id **1** on property 1 |

user_id values are current AUTO_INCREMENT in this DB (4/3/5) and are **not** coded into the miniprogram.

---

## 5. What to click (operator)

After DevTools opens the simulator on **登录**:

1. Confirm the gray note “开发环境 · Fake Provider” and API `http://127.0.0.1:8082/api/v1`
2. Tap **以住户登录** (not 微信登录)
3. Home should show `__DEV_RESIDENT__` / `RESIDENT`
4. **我的房屋 / 绑定** → 房屋 ID fill **1** (current `property_ai_day4` room `101`; looked up from DB, not a global product default) → **提交绑定申请**
5. Home **退出并切换身份** → **以物业登录** → **绑定审核** → 通过
6. Switch back to 住户 → 我的房屋 should list 101 → tap 报修 → 填写说明 → **提交报修**
7. 退出 → **以物业登录** → **待派工单** → 选工单 → 先让员工签到后点选 employee **1** → **派单**
8. 退出 → **以员工登录** → **今日考勤** → **签到** → **我的任务** → **接单** → **开始处理** → **填写回执并完成**
9. 退出 → 住户 → **我的工单** / 最近工单 → **确认解决** → 刷新仍应看到 `COMPLETED`/`CLOSED` 与历史

Do not tap 微信登录 for this local walk. It is not verified.

---

## 6. Page / API matrix

| Flow | Code | Compile in DevTools | Page opened | Click → HTTP | Business state | Manual UI |
| --- | --- | --- | --- | --- | --- | --- |
| Login page + local seed buttons | yes | NEED_OPERATOR | NEED_OPERATOR | seed token HTTP **PASS** | `/me` **PASS** | NEED_OPERATOR |
| Home menus from `/me` permissions | yes | NEED_OPERATOR | NEED_OPERATOR | `/me` **PASS** | roles match seed | NEED_OPERATOR |
| Houses + bind apply | yes (added apply) | NEED_OPERATOR | NEED_OPERATOR | houses empty for seed resident **PASS** | bind not yet approved | NEED_OPERATOR |
| Create / list / detail WO | yes; confirm only `WAIT_CONFIRM` | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN from UI | NOT_RUN | NEED_OPERATOR |
| Supervisor queue + pick employee | yes | NEED_OPERATOR | NEED_OPERATOR | queue HTTP 200; staff/daily needs `tenant_id` (wired from `/me` scopes) HTTP **PASS** | staff employee 1 already `ON_DUTY` today in this DB | NEED_OPERATOR |
| Staff check-in / accept / start / receipt | yes; buttons gated by status | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN from UI | NOT_RUN | NEED_OPERATOR |
| Resident confirm + history | yes; logs from detail | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN from UI | NOT_RUN | NEED_OPERATOR |
| Announcements list/detail | yes; detail uses published list (no GET-by-id API) | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN | NOT_RUN | NEED_OPERATOR |
| Notifications read | yes | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN | NOT_RUN | NEED_OPERATOR |
| Knowledge assistant + citations | yes; Fake LLM | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN | NOT_RUN | NEED_OPERATOR |
| Bind review (manager) | yes | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN from UI | NOT_RUN | NEED_OPERATOR |
| Official WeChat login | button remains | NEED_OPERATOR | NEED_OPERATOR | NOT_RUN | BLOCKED without AppID/secret | NEED_OPERATOR |

Gaps not removed from V1 (no GET `/announcements/{id}`; resident has no house catalog API so house ID is typed; no separate Web Admin).

---

## 7. STOP

WeChat DevTools is not installed, so I stop at **install + import + 以住户登录**. Backend seed-key login against Day 4 **PASS**. UI compile/open/click **NEED_OPERATOR**. Screenshots **not taken** (no fake UI images).

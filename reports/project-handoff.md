# Project handoff (latest facts only)

Updated for Cursor + Codex CLI collaboration. Not a full-repo audit. Not Production V1 / Day 4 / real-AI PASS.

| Item | Current fact |
| --- | --- |
| Repo | `D:\property-ai` |
| HEAD | `8269336c259f791d4e1d115b0e5c2111c16eab46` (parent `14c0e19`). Working tree AUTH-IDENTITY-AI-001-C. Do not revert. |
| Calendar | Spec/git work started **2026-09-15**. Operator last confirmed actual development **day 5 on 2026-09-18**. **Today is 2026-09-19**. Original one-week aim ≈ **2026-09-21**. Full Production V1 cannot meet that date without deleting frozen features; do not delete features or relabel Fake as delivery. |
| Working tree | pre-existing `miniprogram/project.config.json` plus AUTH-IDENTITY-AI-001-C. Do not stage the AppID file |
| That file SHA-256 | `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. Keep as-is. Do not stage, revert, or overwrite |
| That file meaning | operator AppID `wxcfa5fd7dc03cac56` and DevTools compile settings |
| Implementer pytest | host `.venv` unit **320 passed** (FIX-1; do not cite 318 as C). Isolated 0009 **7 passed** on `property_ai_auth001_test`. Shared `property_ai_test` remains **0007** |
| Local API | Day4 Nginx `http://127.0.0.1:8082/api/v1` **unchanged**. Isolated auth API restarted `http://127.0.0.1:8083/api/v1` PID **46740** started 2026-09-19 18:56:05 (after `passwords.py` 18:54:47) → `127.0.0.1:3307/property_ai_auth001_test` @ `20260919_0009` |
| Mini program import | `D:\property-ai\miniprogram` |
| Dev Seed login | Source still has `pages/dev-login` + `LOCAL_PREVIEW`. Ordinary login has no Seed buttons. Local **release pack** at `reports/auth-identity-ai-001/release-pack` has **0** dev-login files and does not register the page. Operator `project.config.json` was not modified. No upload/submit |
| Codex CLI | `codex.cmd` 0.155.1. Delivery owner + independent reviewer. Helper `scripts/ops/codex_exec_readonly.py` prepends `reports/codex/context-preamble.txt`. Schema requires `task_result` / `product_gap_changes` / `schedule_risk` / `next_action` / `approval_requests` |
| VIS batches | MP-LOCAL-VIS-001 and MP-LOCAL-BATCH-002 **page-business vis only**. **Not** formal credential auth, **not** real AI, **not** Production V1. This round DevTools `9420` was down; reset-page screenshots **not** taken |
| Current task | AUTH-IDENTITY-AI-001-C: password no-uppercase, official-session identity, release-pack isolation, self-host AI inventory. Closed security items stay closed. Not formal SMS/real-AI/Production V1 PASS |
| Alembic | Script heads `20260919_0009` revising `20260917_0007`. **0008 reserved**. Exclusive `property_ai_auth001_test` is at `20260919_0009`. Other DBs not upgraded |
| Persistent DB | Do not migrate/wipe `property_ai` or `property_ai_day4`. Do not recreate shared tmpfs `mysql-test` |
| Password policy (operator 2026-09-19; not V1 acceptance) | New/reset: ≥8 chars, **uppercase not required**, reject all-digit and all-lowercase-alpha; `goodpass1` accepted; hash/verify original input (no casefold). Login of existing hashes does **not** apply new-password policy. Do not list uppercase as pending |
| AI hosting | Default: licensed open-weight Qwen on **operator-controlled** servers (rent/own/hosted/private cloud). Dev PC optional. See `reports/auth-identity-ai-001/self-host-recommendation.md`. No running self-host endpoint yet. No GPU rental this round |
| learning/ | Not a design source |

`env.local.js` and `project.private.config.json` stay gitignored. Do not export secrets, tokens, or `.env`.

VIS objects (vis only): house 2 room 102, bind request 11 / relation 11, work order 651 CLOSED, announcement 1, notification 54, assistant citations document_id 3 and 4. See `reports/mp-local-batch-002/`.

---

## Original Production V1 feature matrix

Status values: `已实现未接线` / `部分实现` / `未实现` / `缺真实外部配置`.
None of these gaps are removed from Production V1.

Frozen username rule: PRD/API 为 **手机号 + 密码**。`users.display_name` 仅为展示，**不另设独立用户名登录身份**。

### A. 统一账号认证

| 功能 | 正式需求 | 当前代码/API/页面 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| 统一 User | 一人一号；稳定 `users.id`；角色挂同一 User | 正式登录发同一 `user_id` JWT+`auth_sessions`。普通登录无 Seed。调试仅 `pages/dev-login` | 单测 320；独占库 0009 运行时 7 passed（含正式会话绑定/认领） | 部分实现（软件+独占库；持久库未升 0009） |
| 手机号+验证码 | `POST /auth/challenge` 后 `POST /auth/sms/send`（服务端挑战）+ `/auth/sms/login`；码只存哈希；开发取码仅 test-inbox | 单表单验证码模式；切换保留手机号。无万能码。Development 非正式验收 | isolated 挑战错误/过期不发送；OTP 重用/过期拒绝 | 部分实现。真实短信 **缺真实外部配置** |
| 手机号+密码 | `POST /auth/password/login`；Argon2id；显示/隐藏 | 默认密码表单；唯一主按钮「登录」。新密不强制大写（`goodpass1`） | isolated + identity：正确密码登录；大小写不同拒绝；角色字段 422 | 部分实现 |
| 微信关联 | code 换 session；绑定同一 User；未证明不合并 | 未知 openid 不建 User。登录页微信为次级按钮 | `test_day4_wechat_bind.py`；bind 401 不清会话 | 部分实现。正式 `jscode2session` **缺真实外部配置** |
| 会话恢复 | 有效会话可恢复；无会话必须真实凭证 | 401：latestToken 重试再 `refreshInFlight`。重置后旧会话失效 | isolated 重置后 access/refresh 401 | 部分实现 |
| 忘记密码 | 独立页：挑战→短信→一次性 reset_token→新密+确认→回登录，不自动登录 | `/auth/password/reset/verify` + `/confirm`。文案：至少 8 个字符、不强制大写 | isolated + identity：不一致/弱密/重用拒绝；`goodpass1` 重置后旧会话 401 | 部分实现（页面代码有；本轮 DevTools 未截图） |
| 改密 | `POST /auth/password/change`；新密 `require_new_password` | 路由存在 | 独占库未单列改密用例 | 部分实现 |
| 换手机 | `POST /auth/phone/change` | 无 | 无 | 未实现（仍在 V1） |
| 换微信 | `POST /auth/wechat/rebind` | 仅首次 bind；无 rebind | 无 | 未实现（仍在 V1） |
| 受控合并 | preview + confirm；保留目标 user_id | 无 `AccountMerge`。未知微信不再静默建号 | 无 | 未实现（仍在 V1） |
| 高风险恢复 | `POST /auth/recovery`；实名+活体；不存人脸 | 无 API。`provider/realname` 仍空 | 无 | 未实现。真实核验 **缺真实外部配置** |
| Refresh/Logout | `/auth/token/refresh`；`/auth/logout` | 已实现 | 单测客户端重试。运行时 0009 | 部分实现 |
| DevAuth 隔离 | 正式构建无测试身份；正式环境不接受 DevAuth | 普通登录无三个 Seed 按钮。源码仍有 `pages/dev-login`。**本地 release-pack 不含该页面文件且未注册路由**；未改用户 `project.config.json` | 登录 wxml 单测；`test_release_pack.py`；inventory 79 files / 0 dev-login | 部分实现（源码调试页仍在开发树；发布包已隔离。未上传） |
| 密钥日志 | 失败不泄露密码/验证码/Token | 日志脱敏含 otp/sms_code | logging 单测 | 部分实现 |

不得使用万能验证码验收。Development 短信替身必须明确标记，不能算真实短信验收。

### B. 认证与业务身份

| 功能 | 正式需求 | 当前代码/API/页面 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| 住户登记 | 账号验证后登记人员信息再选房 | `PATCH /me/profile` + `pages/profile`。无独立强制向导 | isolated identity：正式短信会话后改资料 | 部分实现（API；缺本轮页面截图） |
| 房屋选择 | 小区/楼栋/单元/房号；正式页不要求用户知道 `house_id` | catalog 路由 + `pages/houses` 级联 | isolated identity：正式会话级联命中目标房屋 | 部分实现（API；缺本轮页面截图） |
| 关系申请/审核 | 申请后物业审核生效；手机/微信≠房屋归属 | bind-request API；页面选择器提交 | isolated identity：正式住户申请 + TENANT_ADMIN 审核通过，关系 ACTIVE，无 SQL 盖章、无 SYSTEM | 部分实现（API；缺本轮页面截图） |
| 未审批房屋访问 | 无有效关系不得办事 | 工单创建校验房屋关系 | `HOUSE_NOT_OWNED` | 部分实现（缺正式登录后的页面证据） |
| 员工激活 | 预建后受控邀请/核验；不能凭客户端 role/工号提权 | `employee_invites` + `POST /staff/employee-claim` | isolated identity：管理员 HTTP 建未绑 Employee、发邀请、员工正式会话认领后 `STAFF`+`work_order:process`；工作台列表 200 空单 | 部分实现（API；缺本轮页面截图） |
| 任职/岗位/RBAC | EmployeeProperty + Duty/Skill + Scope | 既有；认领后挂本人 `user_id` | 员工停用测试 | 部分实现 |
| 工作台分离 | 同一认证；按已授权权限显示；不能切成别人的 user_id | home 按 permissions 显示住户/员工/主管入口 | isolated identity：`/me` 权限分别含 create/process/dispatch | 部分实现（API 权限；缺三种身份页面证据） |
| 身份证/人脸日常登录 | 禁止作为日常强制登录；不自建人脸库 | 未做日常人脸登录（符合）。恢复流程未做 | 无 | 日常登录：符合（未做）。恢复：未实现且 **缺真实外部配置** |

### C. 真实 AI 与自动派单

| 功能 | 正式需求 | 当前代码/API/页面 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| 文本分析 | 结构化输出；低可信人工；失败工单仍在 | Provider 抽象已有。默认 Fake。推荐自托管 `Qwen3-VL-4B-Instruct` + vLLM，尚未启动 | Fake 测试。真实推理 **无端点** | 部分实现。正式验收 **缺真实外部配置** |
| 图片参与分析 | VLM 描述进入理解 | `QwenVLMProvider` / Dev VLM。创建工单可带 file_id | Fake 行为单测。真 VLM 未跑 | 部分实现 + **缺真实外部配置** |
| 语音参与分析 | ASR 转写后入流水线；失败保留音频 | `HttpASRProvider` / Dev ASR | Fake 单测。真 ASR 未跑 | 部分实现 + **缺真实外部配置** |
| 自动派单 | 硬过滤 + 确定性评分 + 已批准策略下自动生成 Assignment；禁止 LLM 点名 employee_id | `AnalysisService` 完成后调用 `AutoDispatchService.maybe_assign`。读取 `ai_auto_assign`（默认 False→`DISABLED`）。低可信/高风险明确返回。列未映射时 `SCHEMA_PENDING_0009`。LLM 仍不点名 employee_id。人工 assign 仍在 | `test_ai_evidence_provenance.py` | **已实现未接线**（规则引擎+hook）+ 自动 Assignment **未实现**（等 0009 映射）。人工回退 ≠ 已完成自动派单 |
| 低可信/失败人工链 | 低可信/无人/高风险/Provider 失败走明确人工；人工回退 ≠ 取消自动派单 | 低可信保持分析状态、不转 PENDING_ASSIGNMENT；Provider 失败工单仍在。无人可派只写 ineligible 推荐，停在待人工派单 | Fake low_confidence / unavailable 测试 | 部分实现 |
| Embedding/RAG | 真实 embedding + 有效引用；租户/小区/可见性过滤 | Dev embedding dim=8 + Fake 知识；`HttpEmbeddingProvider`/`HttpRerankerProvider` 适配器存在。VIS-004 引用 document 3/4 为 Dev 知识 | Fake citations。真 bge-m3 **缺真实外部配置** 且未联调 | 部分实现。HTTP 200 + 引用数组 ≠ 真 AI 完成 |
| AI 汇总/公告草稿 | Incident 摘要、公告草稿；发布须人工审核 | 公告草稿+审核发布已有。Incident 聚合有 | VIS 公告人工发布；Fake 草稿 | 部分实现。真模型草稿 **缺真实外部配置** |
| 调用证据 | 记 Provider/模型/关联 ID/派单来源；不存密钥与不必要原文 | `AiCallLog` 有。派单来源无 AUTO vs HUMAN 字段 | Fake call log | 部分实现 |

`ANALYZE` 事件、HTTP 200、或一个 citation 数组 **不能** 作为真 AI 完成证据。

### D. 外部配置一次列清（不回显 Secret）

填写位置：本机 `backend/.env` / Compose `env_file`（对照 `.env.example`）。禁止把密钥写入仓库或聊天。

| 能力 | 代码 | Endpoint/适配器 | 权限/凭证 | 真实联调 |
| --- | --- | --- | --- | --- |
| 微信 | `ProductionWeChatAuthProvider` 已实现 | 微信 `jscode2session` | 需认证 AppID + `WECHAT_APP_SECRET`；当前 Day4 `WECHAT_PROVIDER=development`，`WECHAT_APP_ID=wx-dev-app` | 未执行。小程序 tourist/本地 AppID 不能替代 |
| 短信 | `DevelopmentSmsProvider` 已实现；production HTTP **没有** | Settings 读取 `sms_provider`。开发替身进程内持码，不经 HTTP 回传 | 需运营商凭证才能 production | 开发替身单测。真实短信未联调、不算验收 |
| 身份核验 | **没有代码** | 无 | `REALNAME_PROVIDER` 未接入 Settings | 无。保留未验收 |
| LLM | `QwenLLMProvider` 已实现 | 需 `LLM_BASE_URL` OpenAI 兼容 | `LLM_API_KEY` 空则无法 production | 未执行。Day4 Fake |
| VLM | `QwenVLMProvider` 已实现 | `VLM_BASE_URL` | `VLM_API_KEY` | 未执行 |
| ASR | `HttpASRProvider` 已实现 | `ASR_BASE_URL` | `ASR_API_KEY` | 未执行 |
| Embedding | `HttpEmbeddingProvider` 已实现 | `EMBEDDING_ENDPOINT` | `EMBEDDING_API_KEY`；生产维数与 Milvus collection 必须匹配，禁止改历史 migration 改维 | 未执行。当前 Fake dim=8 |
| Reranker | `HttpRerankerProvider` 已实现 | `RERANKER_ENDPOINT` | `RERANKER_API_KEY` | 未执行 |

无授权测试配置时：保留上述阻塞，继续独立软件工作。不购买服务，不擅自打付费模型/短信。

### E. 待批准清单（集中）

| 项 | 为何现在不能擅自做 | 建议 |
| --- | --- | --- |
| Alembic **0008** | 预留给 outbox/claim | 不占用。自动派单/账号表不要塞进 0008 |
| 新 revision **0009** | `20260919_0009` revises `0007`；含 visual_challenges / password_reset_tickets；upgrade() 拒绝非 `property_ai_auth001_test` | **已**在独占库 0007 基线→0009。禁止对 `property_ai` / `property_ai_test` / `property_ai_day4` upgrade |
| 对共享 `property_ai_test` 执行 0009 | 会改变共享集成库 | 仍 **WAIT_HUMAN**。Guard 拒绝该库 |
| 对 day4/生产库 migrate | 持久联调数据 | 禁止直至单独批准 |
| 冻结依赖 CVE pin | Day 4 未关闭项 | 等一次批准 |
| 真实微信/短信/实名/LLM/VLM/ASR/Embedding | 凭证与费用 | 用户本地填写；有授权再有限联调 |
| 租用 24GB 级 GPU 自托管 vLLM（推荐 Qwen3-VL-4B-Instruct） | 费用与数据驻留 | **WAIT_HUMAN**。允许评估 ≠ 允许下单。详见 `reports/auth-identity-ai-001/self-host-recommendation.md` |
| 本机下载权重到 D: | 约 14.8GiB 剩余，易挤爆 | **WAIT_HUMAN**；开发电脑不是生产上限 |
| 将 `ai_auto_assign` 在持久环境改为 true | 改变 Day4 Fake 工单行为 | 软件接线可先用测试 Settings；持久环境单独批准 |
| 正式密码是否必须大写 | 已决定：不强制 | **已关闭** |

---

## This-phase execution

1. Codex plan `AUTH-IDENTITY-AI-001` READY. C 续接密码决定、正式身份、发布包隔离、自托管评估。
2. 密码：≥8、不强制大写、不 casefold；`goodpass1` 单测+独占库重置/登录通过。
3. 独占库正式会话：资料、房屋级联、申请、物业审核、员工邀请/认领、工作台权限；无 Seed、无 SYSTEM、无 SQL 盖章。
4. 本地 release-pack 79 文件、0 个 dev-login 页面文件；未改用户 `project.config.json`；未上传。
5. 自托管推荐 `Qwen3-VL-4B-Instruct` on operator-controlled 24GB GPU；本轮无端点、无下载、无租机。
6. 8083 进程核对：`serve_auth001_isolated_api.py` → `127.0.0.1:3307/property_ai_auth001_test` @ `20260919_0009`。8082 Day4 未覆盖。
7. DevTools 本轮 9420 未监听；忘记密码页面截图未取。历史 login PNG 仍在。
8. 三项已关闭认证安全问题无新证据保持关闭。换绑/恢复/合并、真实 AI/RAG、自动派单 ORM 缺口仍在。
9. 不 push、不 reset、不 restore、不纳入 `project.config.json` / `env.local.js`。

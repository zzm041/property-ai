# Project handoff (latest facts only)

Updated for Cursor + Codex CLI collaboration. Not a full-repo audit. Not Production V1 / Day 4 / real-AI PASS.

| Item | Current fact |
| --- | --- |
| Repo | `D:\property-ai` |
| HEAD | `38d3d09bf2e27154c4d106403cbfa9619026755a` — BATCH-002 evidence. Parent `bc429d3` queue paging. Do not revert. |
| Working tree | pre-existing `miniprogram/project.config.json` plus AUTH-IDENTITY-AI-001 software (uncommitted until WIP). Do not stage the AppID file |
| That file SHA-256 | `342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F`. Keep as-is. Do not stage, revert, or overwrite |
| That file meaning | operator AppID `wxcfa5fd7dc03cac56` and DevTools compile settings |
| Implementer pytest history | 577 passed, 2 skipped (Day 4 focused-fix). Historical only; not this-task evidence |
| Local API | `http://127.0.0.1:8082/api/v1` (Day 4 Nginx, Fake providers, DevAuth). Do not treat as formal auth/AI |
| Mini program import | `D:\property-ai\miniprogram` |
| Dev Seed login | `POST /auth/dev/token` seed_key. Formal user path must stop using this. Backend must not mount/accept DevAuth when `APP_ENV` is staging/production (`dev_auth_enabled` already rejected there) |
| Codex CLI | `codex.cmd` 0.155.1, ChatGPT login. Reviewer only. Existing helper `scripts/ops/codex_exec_readonly.py` |
| VIS batches | MP-LOCAL-VIS-001 and MP-LOCAL-BATCH-002 (VIS-002/003/004) **page-business vis PASS**. Retain evidence. **Not** formal credential auth, **not** real AI, **not** Production V1. Do not redo unless related product code changes |
| Current task | AUTH-IDENTITY-AI-001 software. Codex reviews 1–3 NEEDS_CHANGES; 3-round cap reached, no 4th review. Last findings applied locally. Not formal auth/real-AI/Production V1 PASS |
| Alembic | Heads at `20260917_0007`. Files 0001–0007 frozen. **0008 reserved** for outbox/claim — do not occupy. New identity tables must be a later revision (proposed `0009` revising `0007`). Do not create multiple heads |
| Persistent DB | Do not migrate/wipe `property_ai` or `property_ai_day4`. Do not recreate shared tmpfs `mysql-test`. Persistent migrate + frozen dep upgrades wait one explicit approval |
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
| 统一 User | 一人一号；稳定 `users.id`；角色挂同一 User | 正式登录发同一 `user_id` JWT+`auth_sessions`。小程序统一登录页。Seed 仅 `LOCAL_PREVIEW` | 单测 314；正式运行时表等 0009 | 部分实现（软件已接；持久库未升 0009） |
| 手机号+验证码 | `POST /auth/sms/send` + `/auth/sms/login`；TTL、一次性、重试、频控；码只存哈希 | 路由+`SmsChallenge` HMAC；失败先落库再 401；Redis Lua INCR+EXPIRE 失败关闭。DevelopmentSms 标记 `delivery_channel=development`。无万能码 | `test_official_auth_security.py`。运行时 skip 至 0009 | 部分实现。真实短信 **缺真实外部配置** |
| 手机号+密码 | `POST /auth/password/login`；Argon2id | `UserPassword` Argon2id。错密同一错误文案 | 单测哈希/错密。运行时 0009 | 部分实现 |
| 微信关联 | code 换 session；绑定同一 User；未证明不合并 | 未知 openid `AUTH.WECHAT_BIND_REQUIRED` 不建 User。已知绑定发 `AuthSession`+refresh。`POST /auth/wechat/bind` 仅 `sms`/`password` Principal | `test_day4_wechat_bind.py` BIND_REQUIRED；单测 bind 门禁 | 部分实现。正式 `jscode2session` **缺真实外部配置** |
| 会话恢复 | 有效会话可恢复；无会话必须真实凭证 | refresh 旋转 + logout 作废。小程序 401 先 refresh 一次。无 token 走登录页，不再用角色选择认证 | 单测 refresh 客户端；运行时 0009 | 部分实现 |
| 忘记密码 | `POST /auth/password/reset`；验证码重置；失效旧会话 | 已实现；登录页「忘记密码」。Development 短信标记非正式验收 | 单测 OTP 提交后再 raise。运行时 0009 | 部分实现 |
| 改密 | `POST /auth/password/change` | 路由存在 | 运行时 0009 | 部分实现 |
| 换手机 | `POST /auth/phone/change` | 无 | 无 | 未实现（仍在 V1） |
| 换微信 | `POST /auth/wechat/rebind` | 仅首次 bind；无 rebind | 无 | 未实现（仍在 V1） |
| 受控合并 | preview + confirm；保留目标 user_id | 无 `AccountMerge`。未知微信不再静默建号 | 无 | 未实现（仍在 V1） |
| 高风险恢复 | `POST /auth/recovery`；实名+活体；不存人脸 | 无 API。`provider/realname` 仍空 | 无 | 未实现。真实核验 **缺真实外部配置** |
| Refresh/Logout | `/auth/token/refresh`；`/auth/logout` | 已实现 | 单测客户端重试。运行时 0009 | 部分实现 |
| DevAuth 隔离 | 正式构建无测试身份；正式环境不接受 DevAuth | 后端 staging/production Settings 拒绝 `dev_auth_enabled`。登录页 Seed 仅 develop+`env.local.js`。正式路径走 `/auth/sms` `/auth/password`，不调用 `/auth/dev/token` | `test_dev_auth_disabled_*` | 部分实现（后端门禁+正式入口已拆。不能只靠前端隐藏作为生产证明） |
| 密钥日志 | 失败不泄露密码/验证码/Token | 日志脱敏含 otp/sms_code | logging 单测 | 部分实现 |

不得使用万能验证码验收。Development 短信替身必须明确标记，不能算真实短信验收。

### B. 认证与业务身份

| 功能 | 正式需求 | 当前代码/API/页面 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| 住户登记 | 账号验证后登记人员信息再选房 | `PATCH /me/profile` + `pages/profile`。无独立强制向导 | 软件有；正式会话等 0009 | 部分实现 |
| 房屋选择 | 小区/楼栋/单元/房号；正式页不要求用户知道 `house_id` | `GET /properties|/buildings|/units|/houses` 已挂路由。`pages/houses` 级联选择器，正式 UI 不手填 house_id | `test_house_catalog_runtime.py` 无 token 401 | 部分实现（软件）。带凭证目录联调等 0009 |
| 关系申请/审核 | 申请后物业审核生效；手机/微信≠房屋归属 | 既有 bind-request API。入口改为选择器提交 `house_id`（页面不要求用户知道库 ID） | 既有 resident binding 测试；VIS 仍是 vis-only | 部分实现 |
| 未审批房屋访问 | 无有效关系不得办事 | 工单创建校验房屋关系 | `HOUSE_NOT_OWNED` | 部分实现（缺正式登录后的页面证据） |
| 员工激活 | 预建后受控邀请/核验；不能凭客户端 role/工号提权 | `employee_invites` + `POST /staff/employee-claim`。staging/production `POST /admin/employees` 拒绝 body `user_id`（development/test 夹具仍允许） | 单测拒绝直绑；invite 运行时等 0009 | 部分实现 |
| 任职/岗位/RBAC | EmployeeProperty + Duty/Skill + Scope | 既有；认领后挂本人 `user_id` | 员工停用测试 | 部分实现 |
| 工作台分离 | 同一认证；按已授权权限显示；不能切成别人的 user_id | home 按 permissions 显示住户/员工/主管入口。退出走 logout+清会话回统一登录，不再「切换身份」 | 软件。缺正式三种身份登录证据（等 0009+凭证） | 部分实现 |
| 身份证/人脸日常登录 | 禁止作为日常强制登录；不自建人脸库 | 未做日常人脸登录（符合）。恢复流程未做 | 无 | 日常登录：符合（未做）。恢复：未实现且 **缺真实外部配置** |

### C. 真实 AI 与自动派单

| 功能 | 正式需求 | 当前代码/API/页面 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| 文本分析 | 结构化输出；低可信人工；失败工单仍在 | `AnalysisService` + `DevelopmentLLMProvider` / `QwenLLMProvider`。默认 `LLM_PROVIDER=development`。分析后进入 `PENDING_ASSIGNMENT` | Fake：VIS-003 ANALYZE；`test_ai_runtime.py`。真实 Qwen：**无联调证据** | 部分实现。正式验收 **缺真实外部配置** |
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
| 新 revision **0009**（草案已在仓库） | `20260919_0009` revises `0007`，不占 0008。表：`user_phones`/`user_passwords`/`sms_challenges`/`auth_sessions`/`employee_invites`；assignments source 列 SQL-only 未映射。换绑/合并/恢复表本轮未做 | **不对** `property_ai` / `property_ai_day4` upgrade。Codex schema 意见见 `reports/auth-identity-ai-001/schema-review.md` |
| 对 disposable `TEST_DATABASE_URL` (3307) 执行 0009 | 正式认证运行时测试 skip。禁止重建共享 tmpfs mysql-test | **WAIT_HUMAN** |
| 对 day4/生产库 migrate | 持久联调数据 | 禁止直至单独批准 |
| 冻结依赖 CVE pin | Day 4 未关闭项 | 等一次批准 |
| 真实微信/短信/实名/LLM/VLM/ASR/Embedding | 凭证与费用 | 用户本地填写；有授权再有限联调 |
| 将 `ai_auto_assign` 在持久环境改为 true | 改变 Day4 Fake 工单行为 | 软件接线可先用测试 Settings；持久环境单独批准 |

---

## This-phase execution

1. Codex plan `AUTH-IDENTITY-AI-001` READY（`reports/codex/AUTH-IDENTITY-AI-001-plan.json`）。
2. 独立软件已落地：正式登录 API/页、房屋目录选择器、员工邀请认领、自动派单 hook（默认仍 False；列未映射则 `SCHEMA_PENDING_0009`）。
3. Schema 草案 `20260919_0009` 未 upgrade 任何持久库或共享 mysql-test。
4. Codex review 第 1–3 轮均为 NEEDS_CHANGES。第 3 轮后已达返修上限，不再发起第 4 轮审查。第 3 轮指出的 staggered refresh、`/auth/wechat/bind` 401 清会话、invite/claim 锁顺序已在本地修完，**未经第 4 轮 Codex 复审**。
5. 不 push、不 reset、不 restore、不纳入 `project.config.json` / `env.local.js`。
6. 正式交付不得用一组 Seed 身份切换截图替代。当前也还没有正式三种身份登录证据（等 0009 + 外部配置）。

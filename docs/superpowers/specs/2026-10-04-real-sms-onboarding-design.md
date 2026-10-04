# 真实短信认证与正常新用户准入设计

日期：2026-10-04

状态：待负责人书面复核；真实发送、费用和冻结依赖仍需单独批准

## 1. 目标与范围

在不预置被测用户、不清理历史账号、不改变身份主键策略的前提下，使一个未登记的中国大陆手机号能够完成：

1. 获取登录/注册验证码并首次短信登录；
2. 自动建立唯一的 `User` 与 ACTIVE 手机号关系；
3. 获取重置密码验证码并设置初始密码；
4. 退出后使用同一手机号和密码重新登录；
5. 提交房屋绑定申请，由已有管理流程审核后获得合法住户访问权限。

本设计只补齐真实短信 Provider、配置、错误处理、客户端提示和对应测试。既有认证、会话、密码、房屋绑定与审核模型继续复用。0015、WO20、AI 模型、迁移和发布流程不在本次范围内。

## 2. 现状与约束

- `CredentialAuthService` 已实现 `login`、`reset`、`change_phone` 三种验证码用途，并将验证码哈希保存到数据库。
- 短信登录对未登记手机号创建账号；登录/注册是同一入口，不泄露手机号是否已注册。
- 初始密码设置与忘记密码均复用 `reset` 验证码、重置票据与确认接口。
- 当前只有 `DevelopmentSmsProvider`；选择非 development 模式会因生产 Provider 未实现而失败。
- 验证码有效期、尝试上限和发送/登录频控继续由现有配置与 Redis 执行。
- AI 无关，本功能及验收不得发起任何模型调用。
- 无凭据的单元和集成测试必须继续通过；真实短信只能在负责人批准的号码、次数与预算内发送。

## 3. 方案选择

采用阿里云号码认证服务 PNVS 的服务端 `SendSmsVerifyCode` API，并继续由本系统生成和校验验证码。

选择理由：

- 保留现有验证码哈希、用途绑定、一次性消费、尝试上限和事务边界；
- 不引入数据库迁移；
- 登录/注册与重置密码可以直接映射到阿里云系统赠送模板；
- 供应商只负责发送，认证结论仍由业务后端控制；
- Development Provider 保持离线，不发生静默生产降级。

不采用阿里云生成并通过 `CheckSmsVerifyCode` 校验的路线，因为它会重复现有验证码生命周期并扩大接口、状态和测试改造范围。

## 4. 私密配置契约

本机交付环境从 Git 忽略的 `deploy/.env.delivery` 读取以下值：

| 变量 | 用途 |
| --- | --- |
| `SMS_ALIYUN_ACCESS_KEY_ID` | 仅后端使用的 RAM AccessKey ID |
| `SMS_ALIYUN_ACCESS_KEY_SECRET` | 与上述 ID 配套的 Secret |
| `SMS_ALIYUN_SIGN_NAME` | 控制台当前可用的系统赠送签名 |
| `SMS_ALIYUN_TEMPLATE_CODE` | 登录/注册模板；官方系统模板当前示例为 `100001` |
| `SMS_ALIYUN_PASSWORD_RESET_TEMPLATE_CODE` | 初始密码设置与找回密码共用的重置密码模板；当前示例为 `100003` |
| `SMS_ALIYUN_PHONE_CHANGE_TEMPLATE_CODE` | 既有手机号换绑流程使用的修改绑定手机号模板；当前示例为 `100002` |

模板编号必须以当前阿里云控制台的系统赠送模板为准，且与当前可用的系统赠送签名配套。2026-08-31 已停止支持的历史赠送签名不得继续使用。

`${code}` 与 `${min}` 是运行时模板参数，不增加人工填写变量。后端使用现有六位验证码填充 `code`，使用 OTP TTL 换算分钟填充 `min`。

当 `SMS_PROVIDER=production` 时，任何必需配置为空都必须在 Provider 构建阶段明确失败。不得回退到 Development Provider，也不得在日志、异常、健康接口、报告或测试快照中输出秘密值。

## 5. Provider 边界

新增 `AliyunSmsProvider`，并保持业务 Service 不依赖阿里云 SDK 类型。

Provider 输入：

- 标准化后的 `+86` 手机号；
- `purpose`：`login`、`reset` 或 `change_phone`；
- 现有后端生成的六位验证码；
- 当前请求的 `request_id`，作为供应商 `OutId` 便于脱敏追踪。

Provider 映射：

| 业务 purpose | 模板变量 |
| --- | --- |
| `login` | `SMS_ALIYUN_TEMPLATE_CODE` |
| `reset` | `SMS_ALIYUN_PASSWORD_RESET_TEMPLATE_CODE` |
| `change_phone` | `SMS_ALIYUN_PHONE_CHANGE_TEMPLATE_CODE` |

请求固定约束：

- `CountryCode=86`，传给供应商的号码去掉 `+86`；
- `TemplateParam` 为由 JSON 序列化器生成的 `code`、`min` 对象，禁止字符串拼接；
- `ValidTime` 与本地 OTP TTL 一致；
- `CodeLength=6`、`DuplicatePolicy=1`、`ReturnVerifyCode=false`；
- 禁止由应用层自动重发；供应商自动重试显式关闭；
- 网络连接和读取必须有有限超时；
- 返回结果只保留供应商请求标识、业务流水号和成功状态，不保留明文验证码。

推荐使用官方 Python SDK `alibabacloud-dypnsapi20170525==2.0.0`。这是冻结依赖新增；在负责人明确批准前，只允许完成规格、测试设计和不安装依赖的准备，不修改依赖清单。

## 6. 发送数据流与失败语义

1. 客户端获取并正确回答图形挑战。
2. Service 执行 Redis 发送频控，生成六位 OTP。
3. Service 在自有事务中作废同手机号、同 purpose 的旧 OTP，并保存新 OTP 哈希。
4. Provider 向阿里云提交发送请求。
5. 供应商明确接受后，API 返回 `provider_mode=PRODUCTION` 与脱敏手机号。
6. 客户端开始倒计时并提示“验证码已提交发送，请留意短信”，不得宣称运营商已经送达。

失败处理：

- 配置缺失：Provider 构建失败，不启动真实短信模式；
- 参数或供应商明确拒绝：补偿作废刚创建的 OTP，返回 `AUTH.SMS_PROVIDER_DOWN`；
- 超时或网络结果不确定：同样作废 OTP、返回 `AUTH.SMS_PROVIDER_DOWN`，不自动重试；迟到短信即使送达也不能通过验证；
- 补偿事务失败：记录不含秘密的高严重度结构化日志并继续返回稳定错误，不向客户端暴露供应商细节；
- 所有失败都禁止回退到 Development Provider。

## 7. 客户端行为

- 登录页的 `login` 目的同时代表已有用户登录和未登记手机号注册，不增加“已注册”探测接口。
- 初始密码与忘记密码都进入现有密码重置页，发送 `reset` 目的验证码。
- Development 模式继续明确标记为开发替身；Production 模式改为“验证码已提交发送，请留意短信”。
- 只有短信发送 API 成功返回后才开始倒计时；失败时保留可重试界面并展示稳定业务错误。
- 页面、控制台、截图和采集证据不得显示验证码、AccessKey、完整手机号或环境文件内容。

## 8. 测试与验收

### 8.1 无凭据自动测试

- Settings 对所有短信秘密使用 `SecretStr`，序列化与日志不泄露原值；
- `SMS_PROVIDER=production` 且配置缺失时明确失败；
- 三种 purpose 选择正确模板；未知 purpose 拒绝；
- `+86` 号码转换、模板 JSON、TTL、覆盖策略、超时和 `OutId` 正确；
- SDK 客户端完全使用 Fake/Mock，不访问网络；
- 供应商拒绝、异常和超时统一映射为 `AUTH.SMS_PROVIDER_DOWN`；
- Provider 失败后本次 OTP 不可使用；
- Development Provider 不受影响且生产失败不降级；
- 登录、初始密码/找回密码和换绑页面提示按 Provider 模式正确；
- 既有认证、密码变更、手机号换绑和房屋绑定测试继续通过。

### 8.2 获得真实发送授权后的验收

在 8093 隔离环境、保留现有 3327 数据库的前提下：

1. 使用未登记的负责人自有手机号完成登录/注册短信；
2. 证明数据库只建立一个用户和一个 ACTIVE 手机关系；
3. 使用第二条重置密码短信设置初始密码；
4. 退出并通过密码重新登录同一用户；
5. 从真实小程序页面提交房屋绑定申请，管理员审核后验证住户访问；
6. 检查 API、页面状态、只读数据库证据与脱敏截图属于同一业务对象；
7. 记录实际发送次数和费用，不超过负责人批准上限。

任何结果不确定时停止，不盲目重发。正式发布、上传提审和 Production 验收仍需单独批准。

## 9. 文件与证据边界

- `deploy/.env.delivery` 必须保持 Git 忽略，且由根 `.dockerignore` 的 `.env` / `.env.*` 规则排除在构建上下文之外；
- Dockerfile 不得复制该文件；Compose 仅在运行时通过 `env_file` 注入；
- 报告和公开证据只能记录键是否已配置、Provider 模式、脱敏请求标识和脱敏手机号，不读取或复制值；
- 示例文件只能包含空值或明确占位符，禁止从本机私密文件同步生成；
- 本功能不读取 `learning/`，不触碰 0015、WO20 或既有验收证据。

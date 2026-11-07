# 08 — 非功能需求与架构

## 1. 架构方案（模块化单体 + Worker）

```text
微信小程序
    │ HTTPS
    ▼
Nginx（TLS、静态、反向代理）
    ▼
FastAPI API 进程
    │
    ├── MySQL 8（事务真相源）
    ├── Redis（缓存、锁、限流、会话辅助）
    └── RabbitMQ → Celery Worker
            ├── ai_analyze
            ├── dispatch
            ├── sla_tick
            ├── notify
            ├── incident_detect
            ├── asr_transcribe
            └── rag_index
    └── Milvus（仅知识向量）
```

不拆微服务。领域边界用 Python 包划分。Worker 与 API **共享同一套 domain/service 代码**，禁止复制两份状态机。

数据库访问统一使用同步 SQLAlchemy Session + PyMySQL。数据库主导的 FastAPI Endpoint 优先声明为普通 `def`，由 FastAPI 在线程池执行；禁止在 `async def` Route 或 Service 中直接执行同步数据库 IO，也禁止 SyncSession / AsyncSession 混用。

## 2. 逻辑分层

| 层 | 职责 | 禁止 |
| --- | --- | --- |
| `api` | HTTP、鉴权依赖、请求校验 | 直接写 SQL、直接调 LLM SDK |
| `schema` | Pydantic 入参出参 | 业务决策 |
| `service` | 用例编排、事务边界、统一 commit/rollback | 依赖 FastAPI Request 对象 |
| `domain` | 状态机、派单规则、纯函数 | IO |
| `repository` | SQLAlchemy 查询、隔离条件 | HTTP；在完整用例中自行 commit |
| `model` | 表映射 | 对外暴露 |
| `provider` | 外部 IO | 业务规则 |
| `worker` | Celery 任务入口 | 另写一套领域规则 |
| `core` | 配置、日志、错误码、中间件、安全原语 | 业务实体 |

## 3. 容量目标（V1，单机 Compose）

| 指标 | 目标 |
| --- | --- |
| 业主规模 | 数千 / 多小区 |
| API 可用性 | 业务时间 99.5%（单机，非多活） |
| 创建工单 P95 | < 500ms（不含 AI） |
| AI 分析 | 异步，不阻塞创建 |
| 派单 | 分析完成后秒级到分钟级 |
| 知识问答 P95 | < 5s |

达不到时优先保创建工单和人工派单，不保 AI。

## 4. 可观测性

- 结构化 JSON 日志：timestamp, level, request_id, tenant_id, user_id, event, latency_ms
- 每个请求中间件注入 `request_id`
- `AiCallLog` 独立表
- Health：`/healthz` 只代表 API 进程存活；`/readyz` 至少分别报告 MySQL、Redis、RabbitMQ。
- MySQL 是 Day 1 核心业务的 Critical 就绪依赖；不可用时 readyz 失败。Redis 与 RabbitMQ 短暂不可用时报告 degraded，保留不依赖它们的基础查询/人工业务；具体依赖它们的缓存、锁、限流或异步功能必须显式失败或降级，不能假成功。Milvus 不进入 Day 1，未来不可用时不得拖垮报修。

## 5. 数据

- 核心表主键为 BIGINT AUTO_INCREMENT，外键使用同型 BIGINT
- Alembic 管理全部表结构变更
- 禁止 `create_all` 代替 Migration
- 禁止生产手工改表
- 事务边界由 Service 负责；例如绑定审批中的申请状态、正式关系和 AuditLog 必须整体提交或整体回滚
- 备份：MySQL 每日逻辑备份 + binlog 保留策略（部署文档）
- 文件与 DB 备份分开

## 6. 异步与一致性

- 工单创建与 AI、通知、SLA 时钟：API 提交事务后发 MQ
- 消费失败：Celery retry + 失败落库；V1 预留 DLQ 队列名，先用死信+人工重放
- 通知至少一次投递，客户端按 id 去重

## 7. 国际化与时区

- 默认 `Asia/Shanghai`
- 数据库时间戳统一保存 UTC
- 小区可有 timezone 字段；签到等自然日业务按小区时区生成 `business_date`，禁止用 UTC date 直接判断“今天”

## 8. 客户端

- 微信原生小程序，不在 V1 做独立 App / 管理后台 Web（企业管理员可先用同一小程序的管理视图；若后续要 Web，须另批）
- 不引入未批准的前端框架到小程序

## 9. 测试要求

每个模块最低：

- 领域单测：状态机非法转换、派单硬过滤
- API 测试：鉴权失败、校验失败、幂等
- Provider 用 Development 实现，CI 不依赖真实 Key
- Integration Test 必须使用 MySQL 8，不得用 SQLite 冒充数据库、RBAC、Tenant/Property 隔离、事务或审批集成测试
- `TEST_DATABASE_URL` 必须与 Development `DATABASE_URL` 隔离；downgrade、破坏性测试和数据清理只能针对明确可销毁的 Test DB
- 禁止在 Development DB 上执行 Migration downgrade 测试

## 10. 明确的非目标

- 不承诺多活、异地容灾、自动扩缩容
- 不上 K8s
- 不自建服务网格

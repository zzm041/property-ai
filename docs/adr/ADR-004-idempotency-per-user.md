# ADR-004：幂等键按用户维度（`(user_id, key)`）

- 状态：已采纳（2026-11-11，commit `d0193d1` + 迁移 `0016`）
- 相关：`docs/06-API-CONTRACT.md:165`、`backend/alembic/versions/20261111_0016_idempotency_key_scope.py`

## 背景

创建类接口都要求 `Idempotency-Key`（`docs/06-API-CONTRACT.md:6`），语义是「同 key 同请求返回
同一结果，同 key 异体返回 409」。最初实现把唯一约束放在 `idempotency_key` **单列**上
（`uq_idempotency_records_key`），而读路径从第一天起就是按用户查的
（`repository/work_order.py:206 get_idempotency`、`repository/property.py:43`）。

两者不一致带来两个真实问题：

1. **阻断**：用户 A 用过 `mp-1712…` 之后，用户 B 用同一个字符串会被数据库唯一约束挡下，
   表现为 409 `WO.IDEMPOTENCY_CONFLICT`——B 什么都没做错。
2. **泄露**：409 本身告诉 B「这个 key 已存在」，等于让人探测别人是否存在同 key 记录。

客户端 key 是各端自己随机生成的（小程序 `mp-<ts>-<rand>`、脚本 `t7-<uuid>`），碰撞概率极低，
所以「全局唯一」带来的并不是防重能力，只是把自己人的请求互相挡掉。

## 决策

1. 唯一约束改为复合：`UniqueConstraint("user_id", "idempotency_key",
   name="uq_idempotency_records_user_key")`（`model/work_order.py:787`），迁移
   `0016` 先 drop 旧索引再建新索引（`:27` / `:32`）。
2. 服务端冲突归类同时认新旧两个名字：`_IDEMPOTENCY_UNIQUE = frozenset({"uq_idempotency_records_user_key",
   "uq_idempotency_records_key"})`（`service/work_order.py:164`），这样四容器先切应用、后跑迁移
   也不会把 1062 误判成 500（`:3630` 的 1062 映射）。
3. downgrade 拒绝在「存在跨用户共享 key」时回退：非 0 就 `RuntimeError`，而不是静默丢掉别人的行。
4. 契约写清作用域：`docs/06-API-CONTRACT.md:165`「键的作用域是提交人：`(user_id, key)` 唯一，
   两个用户各自使用相同字符串互不影响；同一用户同键异体请求仍返回 409」。

## 替代方案

| 方案 | 未采纳原因 |
| --- | --- |
| 保持全局唯一，让客户端自己保证随机 | 把「不撞键」变成客户端义务；撞了以后无法自愈（B 永远拿不到自己的工单） |
| key 前拼用户 id 存储（`user_id:key`） | 逻辑等价但破坏「按 key 查」的既有索引与审计口径，且迁移更大 |
| 只在服务层加 `user_id` 条件、约束不动 | 约束仍在，跨用户同 key 仍会 409，问题不解决 |
| 干脆去掉唯一约束 | 并发下同一用户同 key 会建出两张单，幂等语义消失 |

## 后果

- 正向：跨用户同 key 各自成功（`test_work_order_runtime.py::test_same_idempotency_key_is_scoped_per_user`：
  两个 200、id 不同、A 重放返回同 id）；同用户重放与异体 409 语义不变。
- 代价：约束名变了，任何直接按名字写 SQL 的脚本要跟着改（仓库内只有 `_IDEMPOTENCY_UNIQUE` 与 schema 守卫测试引用它）。
- 代价：迁移不可无损回退——downgrade 在有共享 key 时会拒绝，这是有意保留的失败面。
- 代价：交付库需要真跑一次迁移（已执行：`delivery database migrated to
  20261111_0016_idempotency_key_scope`，索引 `uq_idempotency_records_user_key` 生效）。
## 证据（文件:行号）

- `backend/app/model/work_order.py:778`（`IdempotencyRecord`）、`:787`（复合唯一约束）
- `backend/alembic/versions/20261111_0016_idempotency_key_scope.py:20`（revision）、`:21`
  （down_revision 0015）、`:27`（drop 旧索引）、`:32`（建复合索引）、`:57`/`:62`（downgrade）
- `backend/app/service/work_order.py:164`（唯一名集合）、`:1382`（读）、`:1555`（写）、
  `:3630`（1062 归类）
- `backend/app/repository/work_order.py:206`、`backend/app/repository/property.py:43`（按用户查）
- `backend/app/cli/delivery_migrate.py:14`（HEAD 常量）、`:87`（起点白名单含 0015）
- `docs/06-API-CONTRACT.md:165`（作用域写进契约）

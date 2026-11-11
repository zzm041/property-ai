# 真实短信接入前置核查（脱敏）

日期：2026-10-05。范围仅为本机 8093 隔离环境。

## 构建上下文隔离

- Compose 的实际 `build.context` 是仓库根目录，Dockerfile 是 `deploy/Dockerfile`；不存在 `deploy/Dockerfile.dockerignore`。
- 原根 `.dockerignore` 的 `.env` 和 `.env.*` 未排除子目录中的 `deploy/.env.delivery`。只读 Docker bind-mount 探针曾可取得目标路径，因此此前按规则推断“已排除”不成立。
- 已增加根 `.dockerignore` 的 `deploy/.env.delivery` 和 `deploy/.env.delivery.*` 定向规则。同一无网络、无镜像产出的探针复测返回该路径 `not found`，构建上下文传输由 4.55 kB 降至 2 B。这里只记录路径与结果，不记录文件内容。
- 现有 Dockerfile 只复制依赖锁文件和 `backend/`。构建上下文隔离仍然必要，因为先前即使未复制进最终层，文件也曾可被本地构建器读取。

## 已上传环境文件的最小影响轮换范围

通过只读比较本机文件中的键名与运行中容器环境，以下非空敏感项确实在 8093 服务中生效：

| 轮换对象 | 当前消费者与配套更新 |
| --- | --- |
| `APP_SECRET_KEY` | API、worker、publisher、beat；轮换后旧 JWT、刷新会话签名和未完成的 OTP/重置票据需视为失效并验证新会话。 |
| `MYSQL_PASSWORD` | MySQL 业务用户与 `DATABASE_URL`；须同时改变数据库账号密码和 API/worker/publisher/beat 连接。 |
| `MYSQL_ROOT_PASSWORD` | MySQL root 账号；持久卷中的账号须实际改密，不能只改 Compose 环境。 |
| `REDIS_PASSWORD` | Redis 服务命令、`REDIS_URL`、API/worker/publisher/beat；须保留 Redis 数据卷并成组重启/验证。 |
| `RABBITMQ_PASSWORD` | RabbitMQ 持久用户、`CELERY_BROKER_URL`、API/worker/publisher/beat；须实际改用户密码。 |
| `MINIO_ROOT_PASSWORD` | MinIO root、Milvus MinIO 连接、应用的 `STORAGE_SECRET_KEY`；`STORAGE_ACCESS_KEY` 与 `MINIO_ROOT_USER` 当前为同一非空身份。须成组更新并核对原桶可读写。 |

`DATABASE_URL`、`REDIS_URL`、`CELERY_BROKER_URL`、`STORAGE_ACCESS_KEY` 与 `STORAGE_SECRET_KEY` 是上述服务凭据在消费者侧的有效副本，不应单独只改文件值。首次核查时阿里云 SMS 凭据为空且尚未在容器生效；此后本机填写状态可能变化，须在启用前重新按键名核对，不能将新填值写入报告或日志。其他非空键为环境模式、端口、模型地址或功能参数，不按凭据处理。

轮换顺序：先准备未写入日志/证据的新本地凭据和连接串；再在各持久服务中变更账号或认证配置；随后原子更新忽略文件中的服务端与消费者字段、受控重启 8093、验证健康及授权访问。不得清库、丢弃卷或只改 `.env.delivery`。轮换前保持手机入口关闭；发生任一步失败时停止实发并按同一成组配置回滚或修复。

## 2026-10-05 发送前运行状态（脱敏）

- 已在隔离 8093 栈成组轮换 `APP_SECRET_KEY`、MySQL 业务/root、Redis、RabbitMQ、MinIO root 及对应消费者 URL/密钥；新凭据的服务端认证和各运行容器的 effective-config 一致。3327 数据库记录数与各持久卷挂载在轮换前后相同；未迁移、清库、删除卷或调用模型。
- RabbitMQ 首次重建因容器默认主机名改变，当前节点曾看不到原 DLQ 的 2 条积压；旧节点数据仍在原卷内。已在 Compose 中固定原 Mnesia 节点身份并恢复该节点，原 DLQ 2 条重新可见，正常业务队列为空。不得把短暂健康状态等同于持久消息验证。
- API、worker、publisher、beat 使用 `property-ai-delivery:sms-offline-20261005`，实际镜像 ID 为 `sha256:d1b954ea9f66a352ab8bb58b97dc0bc6e35936685ba331bb32dc7536ba4956c4`。运行镜像中的 379 个受跟踪 backend 文件按跨平台行尾规范化后与 `553be14` 全部匹配。`APP_ENV=staging`，`SMS_PROVIDER=production`，Provider 实例为 `AliyunSmsProvider`；无 Development 回退。
- 本机短信各必填键均已配置且与运行容器一致；模板代码按登录/注册、重置密码、换绑用途分别匹配 100001、100003、100002。唯一大陆手机号 allowlist 格式与错误号码拒绝门槛通过，但不记录号码或签名值。RAM 自定义策略的云端绑定来自负责人声明，本机只读检查不能独立证明该策略确实授予发送动作；不得将其写成已独立验证。
- Redis AOF 为 `appendonly=yes`、`appendfsync=always`。一次性预算键已建立、无 TTL，状态 `ACTIVE`，发送尝试 0/4、预留费用 0/0.30 元、未知结果 0；运行源码确认 `AutoRetry=0`、SDK `autoretry=False/max_attempts=1` 且应用只有一处 Provider 发送调用。
- 8093 `/healthz`、`/readyz` 与 Milvus 健康接口均返回 200；MySQL root/业务、Redis、RabbitMQ 新认证可用，MinIO 原桶可读，Milvus 原 collection 可连接且实体数为 2。本人受限手机入口仍关闭，未调用 `SendSmsVerifyCode`，未发送真实短信。下一步仅在负责人再次明确确认手机准备好后，按已授权范围恢复入口并进行首条注册短信；如需云端 RAM 权限独立证明，应先提供可读策略证据或以首发结果作为实际验证，不得提前宣称成功。

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

`DATABASE_URL`、`REDIS_URL`、`CELERY_BROKER_URL`、`STORAGE_ACCESS_KEY` 与 `STORAGE_SECRET_KEY` 是上述服务凭据在消费者侧的有效副本，不应单独只改文件值。阿里云 SMS 凭据目前为空，尚未在容器生效；本轮短信变量不在此次旧值轮换范围。其他非空键为环境模式、端口、模型地址或功能参数，不按凭据处理。

轮换顺序：先准备未写入日志/证据的新本地凭据和连接串；再在各持久服务中变更账号或认证配置；随后原子更新忽略文件中的服务端与消费者字段、受控重启 8093、验证健康及授权访问。不得清库、丢弃卷或只改 `.env.delivery`。轮换前保持手机入口关闭；发生任一步失败时停止实发并按同一成组配置回滚或修复。

## 当前门控

- 未进行凭据轮换，未切换 SMS Provider，未发送短信。
- 实发前还需完成离线实现与独立复审、必要轮换、手机号码与模板配置检查及负责人手机就绪确认。

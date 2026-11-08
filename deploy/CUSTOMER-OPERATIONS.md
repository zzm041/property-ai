# 客户安装后的启动、检查与停机

适用范围：已按 CUSTOMER-INSTALL.md 完成空库安装和 CUSTOMER-STORAGE.md 完成独立桶/应用账户配置的新客户项目。下列命令是部署方操作手册，不表示开发者已经在甲方机器执行或完成全栈验收。原本地 v26 不使用此流程。

## 固定本次操作目标

从仓库根目录操作，先用 `docker context show` 确认连接的 Docker 主机。核对项目名、数据库名、镜像标签、配置文件及备份目录。不要继承另一项目终端中的数据库/凭证变量；Compose 的宿主机环境变量可能覆盖文件值。不要把包含凭证的完整 `docker compose config` 输出发给开发者。

以下项目名 `customer-property-ai` 必须换成与 `.env.customer` 的 CUSTOMER_PROJECT_NAME 相同、已批准且独立的名称。显式指定项目名，避免误用宿主机 COMPOSE_PROJECT_NAME。

Linux Bash：

```bash
customer_compose() {
  docker compose --project-name customer-property-ai --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml "$@"
}
customer_compose config --quiet
customer_compose config --services
customer_compose config --images
```

Windows PowerShell 使用同样的固定参数：

```powershell
function Invoke-CustomerCompose {
    docker compose --project-name customer-property-ai --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml @args
    if ($LASTEXITCODE -ne 0) { throw '客户 Compose 操作失败，请检查后再继续' }
}
Invoke-CustomerCompose config --quiet
Invoke-CustomerCompose config --services
Invoke-CustomerCompose config --images
```

下文 `customer_compose` 在 PowerShell 替换为 `Invoke-CustomerCompose`。配置检查失败就停止；不得通过恢复开发认证或 Development AI 使检查“通过”。正常服务列表不应包含 installer、storage-installer、migrate 或 storage-init。

## 首次正常启动

应用配置已导出、两个初始化流程已完成，且部署方准备好真实模型与身份通道后，按顺序执行；每步成功再进行下一步：

```bash
customer_compose up -d --no-build mysql redis rabbitmq etcd minio milvus scanner
customer_compose ps
customer_compose exec scanner clamdscan --version
customer_compose logs --tail 50 scanner
customer_compose up -d --no-build api worker publisher beat
customer_compose ps
customer_compose up -d --no-build nginx
```

检查基础服务健康后才启动应用。扫描库更新成功与引擎健康分别核对。`beat` 会启动一次性 beat-volume-init，为本项目的调度状态卷准备权限；完成退出是正常状态。应用不自动运行数据库迁移。使用 `--no-build` 防止启动时意外构建未经核对的源码；镜像缺失时先按交付清单准备镜像。

模板 HTTP_HOST_PORT 为 8103，只监听本机回环地址。部署方改过端口时同步替换检查地址：

```bash
curl --fail http://127.0.0.1:8103/api/v1/healthz
curl --fail http://127.0.0.1:8103/api/v1/readyz
```

healthz 仅证明 API 存活。readyz 返回 200 也可能是 degraded，且不证明 Worker、真实模型、对象上传或 AI 自动派单已通过。由部署负责人另外记录一次真实合成报修的分析/规则派单结果、一次可信附件扫描、知识引用和用户权限确认；未核对项目不要标成完成。不重复开发者已经提供的旧专项测试来代替新客户环境验收。

Supervisor Agent 是独立可选入口，默认关闭；如启用，应按 docs/13-SUPERVISOR-ASSISTANT.md 单独配置真实模型及权限，不能将 Development Agent 输出当真实推理。通知是否具有外部推送能力，以实际 Provider 为准；站内消息与外部推送成功须区分。

甲方正式 TLS、小程序合法域名及上线审批按 docs/09-DEPLOYMENT.md、docs/11-OPERATIONS.md 执行。私密面试演示使用独立的 online-demo 流程，不能直接把本客户环境或 GPU 端口转到公网。

## 日常状态与故障

```bash
customer_compose ps
customer_compose logs --tail 100 api worker publisher beat
```

- 数据库/扫描未健康：停止后续启动，检查对应日志和配置，不重复运行空库安装。
- API 正常但 AI 未完成：核对 Worker、Publisher、队列、模型认证及超时。保留工单与失败记录，不用反复创建报修验证，也不自动重放历史失败任务。
- 上传 pending：核对扫描引擎/病毒库/大小限制；不手动改为 clean 绕过扫描。
- 变更配置：重新导出到新应用配置文件，更新两个应用文件路径，核对新镜像/配置及备份后，在维护窗口重建对应应用服务。单纯 `restart` 不会载入新的容器环境变量。

日志只在运维受控环境查看，对外交流时去除个人资料和凭证，不公开上传完整日志或渲染配置。

## 维护停机与恢复

第一步只暂停入口和定时/新任务投递，暂时保留 API、Worker 与基础设施：

```bash
customer_compose stop nginx beat publisher
```

在这里停下核对：确认在途 API 请求结束，再检查 Worker 的 active/reserved/scheduled、消息队列未确认消息及数据库持久任务状态。等待执行中的工作结束，确认未来计划/未投递任务已持久化且重启可继续；不能把“队列为零”当成没有执行中的任务，也不能跳过检查直接执行下一段。若状态不明或存在长任务，保留服务并调查，不依靠 180 秒倒计时排空。

负责人确认可以停机后，才执行第二步：

```bash
customer_compose stop --timeout 180 worker api
customer_compose stop --timeout 180 mysql redis rabbitmq milvus etcd minio scanner
customer_compose ps --all
```

180 秒是容器终止宽限示例，不是业务 SLA，也不是任务必定已排空的证明。若仍有长任务，先等待并核对，不依靠强制终止完成维护。保留容器、所有卷和配置；日常停机不执行 `down -v`、删除卷或重新跑 installer。重启沿用首次正常启动的顺序及原项目名。

备份至少包括 MySQL、媒体对象字节、私密配置/版本清单和调度状态；备份放在独立受控位置并验证可恢复。数据库逻辑备份入口为 `scripts/ops/mysql_backup.py`，密码通过其 `--password-env` 指定的秘密环境变量传入，不能写进命令行。明确容器、数据库、Docker 主机以及新的输出文件；退出失败或部分文件不能视为有效备份。

恢复先在独立可销毁目标验证备份、revision 和对象完整性，再由负责人批准业务目标切换。`mysql_restore.py` 会执行可信转储中的 SQL，失败可能部分生效，禁止自动循环重试。它不备份/恢复媒体文件，不能代替对象存储备份。详细机制和风险见 docs/11-OPERATIONS.md 第 8–10 节。

版本回退需要对应的应用镜像、配置和兼容数据库证据；不能把旧镜像重新启动等同数据库回滚。安装器不保证 MySQL DDL 整体回滚；失败现场保留，另行核对修复方案。

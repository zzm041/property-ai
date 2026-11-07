# 甲方全新数据库安装

状态：当前源码已通过一次独立 MySQL 8.0.41 空库安装、单一初始化管理员及重复安装拒绝验证；这不代表最终交付包的新机器全栈安装通过。

此入口用于甲方选择的新空库。已有实例升级、当前本地 v26、历史交付验收库继续各自的受控流程。企业知识库项目不在此安装范围。

## 操作者准备

模型超时在客户模板中显式配置：文字/知识问答/图片/向量60秒、语音120秒、重排30秒，避免沿用开发默认的较短等待时间。它们在现有Settings允许范围内，不修改运行中的v26。实际目标机仍须核对模型延迟、网关预算与业务调用链，超时不应通过无限重试掩盖。

1. 将 `.env.customer.example` 与 `.env.customer.bootstrap.example` 分别复制为同目录的 `.env.customer` 和 `.env.customer.bootstrap`，用甲方凭证填写。应用生产配置必须完整；示例空值/replace 占位值不可投入使用。数据库 URL 中密码要进行 URL 编码，并与 MySQL 用户密码对应。
2. 为本次安装指定新的 `CUSTOMER_PROJECT_NAME`、`MYSQL_DATABASE`、队列、bucket、Milvus collection 和无冲突宿主机端口。客户 overlay 的 MySQL 卷以该项目名隔离，不使用本地 delivery 卷名。已有同名项目/卷应先核对，禁止 `down -v` 或覆盖。
3. 确定受审查的应用版本/镜像标签，准备 `deploy/customer-install` 输出目录。Linux 上该目录须允许容器用户 UID 10001 写入；仅这一目录提供给安装容器，不挂载整个宿主机。
4. 扫描器、模型服务、企业微信/短信凭据、对象存储 bucket 与专用权限由部署方准备。`FILE_SCANNER_HOST` 是 API 容器可达的固定内网数字 IP。公网 TLS 和小程序合法域名配置另行完成。不得借用 Development Provider 冒充正式 AI。

## 构建与离线清单

以下命令从仓库根目录运行（PowerShell 可将各条写在一行）。示例只准备源代码，当前会话未执行这些容器命令。

```bash
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml --profile install build installer
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml --profile install run --rm --no-deps installer plan --plan /install/plan.json
```

`plan` 不连接数据库、不调用模型、不下载模型。输出清单在 `deploy/customer-install/plan.json`，包含数据库主机/端口/库名、环境、固定 head、应用/迁移 Python 源码摘要和步骤，无账号密码。终端输出 `CUSTOMER_INSTALL_PLAN <SHA-256>` 是规范化清单摘要；不要用格式化 JSON 文件的字节哈希替代。文件已存在时不覆盖，版本变化请另用新文件名。

## 执行批准后的空库安装

部署负责人先核对清单目标与版本，再批准实际数据库操作。此时只启动数据库，避免应用先连接半成品 schema：

```bash
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml up -d mysql
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml --profile install run --rm --no-deps installer apply --plan /install/plan.json --approve-plan <刚才核对的SHA-256>
```

首次启动数据库后应等待 MySQL 健康检查通过再运行 `apply`。不要盲目循环重试。安装器取数据库级互斥锁、核对真实数据库名和空库，再使用同一连接执行现有迁移。管理员从私密 bootstrap 文件读取，只建立初始 SYSTEM_ADMIN 与权限，不注入测试住户、工单或员工。

成功标记为 `CUSTOMER_INSTALL_COMPLETE 20261003_0015_work_order_media_cycle_links`。随后移走主机上的 bootstrap 文件并保存到甲方密钥管理系统；正常应用运行使用基础 Compose 与客户 overlay，不启用 install profile。安装服务不参与普通 `up`。

## 失败与剩余激活步骤

- `CUSTOMER_INSTALL_PLAN/APPROVAL`：清单/源码/配置变动或摘要不符，重新审查并生成新清单，不绕过核对。
- `CUSTOMER_INSTALL_ADMIN`：初始化身份或密码不合规；修正私密文件，无需对外提供密码。
- `CUSTOMER_INSTALL_BUSY/NOT_EMPTY`：其他安装仍持锁或库中已有对象；拒绝自动覆盖或清空。
- `CUSTOMER_INSTALL_FAILED`：保留现场。MySQL DDL 可能已经提交，不能宣称整体回滚；不要对非空库再次运行空库安装。由运维核查实际 revision 与失败阶段，另行准备恢复方案。

数据库完成后仍须准备对象存储 bucket/权限、可信扫描与病毒库、模型实际推理、知识索引、企业身份通道、应用四服务与 TLS，然后进行部署验收。当前 `delivery.ps1 init` 和旧 `storage-init` 是固定本地验收目标，不能用于客户库/bucket；不要用它们替代客户准备步骤。本文件不声称这些步骤已经完成。

正常服务启动命令准备中以受审交付清单为准；在模型可移植安装与线上隔离验收完成前，不发布“甲方一键安装已通过”的结论。

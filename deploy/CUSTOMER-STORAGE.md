# 客户对象存储初始化

此入口不使用固定的本地验收bucket。针对已批准的客户独立目标，先生成无密钥清单与应用策略，再由存储管理员配置专用账户，最后显式创建新桶并验证读写。已有桶一律不由`apply`接管，不自动删除桶、覆盖对象或重试失败安装。

## 配置与权限

1. 在客户总配置填写STORAGE_ENDPOINT、STORAGE_BUCKET、STORAGE_REGION（默认us-east-1）和新的专用STORAGE_ACCESS_KEY/SECRET_KEY，按[CUSTOMER-INSTALL.md](CUSTOMER-INSTALL.md)导出`.env.customer.app`。
2. 在`.env.customer.storage-bootstrap`填写独立存储管理员凭证。管理员与应用账户必须不同；日常四个应用服务不读取这个文件。
3. 使用安装镜像离线生成清单和策略。只读取配置和源码，不连接存储：

```bash
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml --profile storage-install run --rm --no-deps storage-installer plan --plan /install/storage-plan.json --policy /install/application-policy.json
```

清单绑定endpoint、bucket、region、应用access key摘要及源码，输出`CUSTOMER_STORAGE_PLAN <SHA256>`。策略仅允许指定bucket下的GetObject、PutObject，不允许列桶、管理用户、创建桶、删除对象或其他bucket。操作者核对目标及摘要后再批准写入。

4. 启动客户MinIO或使用甲方已准备的独立S3兼容存储。使用存储管理控制台导入`application-policy.json`，创建专用应用账户并只绑定该策略。不要复用root、consoleAdmin账户或给该账户附加其他组/策略。控制台凭证和密钥不发送给开发者、不放入仓库。也可用已批准的MinIO Client按官方管理流程导入策略及绑定用户；现有冻结MinIO镜像包含mc工具。

**权限证明边界：**安装器导出最小权限模板并做正向读写探针；它不能通过S3协议证明账户没有其他IAM权限。管理员必须核对实际用户/组/策略。隔离验证中已按该模板配置账户，并实际确认删除对象、读取其他桶被拒绝；这不代表任意甲方既有账户自动具有最小权限。

## 新桶创建与探针

```bash
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml up -d minio
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml --profile storage-install run --rm --no-deps storage-installer apply --plan /install/storage-plan.json --approve-plan <核对后的SHA256>
```

仅HEAD明确不存在时尝试创建；并发创建返回409时立即拒绝，不继续读写。普通账户写入、读回一份固定合成内容，由管理员删除本轮随机`_installation_probe/`对象。只有内容一致且清理成功才输出`CUSTOMER_STORAGE_COMPLETE`。不修改匿名公开策略；新桶默认私有，部署方必须保持该设置。

失败保留桶及现场，不声称事务回滚。`CUSTOMER_STORAGE_EXISTS`表示目标已存在或创建冲突，不绕过拒绝；`PLAN/APPROVAL`需重核清单；`PROBE`需检查应用策略/网络；`CLEANUP`表示无法确认本轮探针清理，应由管理员检查该前缀，不得批量删除历史对象。

修复权限后可显式重新验证既有目标的应用读写（不会再次创建桶）：

```bash
docker compose --env-file deploy/.env.customer -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.customer.yml --profile storage-install run --rm --no-deps storage-installer probe --plan /install/storage-plan.json --approve-plan <核对后的SHA256>
```

完成后将存储bootstrap凭证移入甲方密钥管理系统；不启动该工具profile用于日常服务。备份应同时覆盖数据库元数据和对象字节，具体恢复顺序见docs/11-OPERATIONS.md；数据库备份不会备份媒体文件。

当前源码已在独立临时MinIO完成新桶/正向读写、重复初始化拒绝、手工配置策略的越权拒绝及探针清理。整套客户新环境安装仍单独验收，不能以本模块结果代替。

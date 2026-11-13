# 下一验收阶段：隔离业务运行（已授权并完成本批运行）

配置草案：reports/isolated-business-runtime-20261102/compose.yml。目的为把已修改的本地源码放入独立API/Worker，验证住户与物业隔离、主管诊断和正常/失败工单闭环。不是部署到旧栈或delivery，不以此次通过宣布生产发布。

## 精确范围

- 项目名property-ai-business-20261102；仅6个临时容器mysql/redis/rabbitmq/api/worker/publisher及该项目自己的网络、合成附件卷。数据库在tmpfs，无宿主数据库端口。
- 只使用现有本地镜像；应用固定当前已核实镜像ID，pull_policy=never，不下载/构建/升级依赖。后端源码只读挂载；运行前记录源码指纹，期间发生变更则该结果不签收。
- 唯一入口127.0.0.1:18093；内部网络不连接旧栈、delivery或外部网络。所有模型/短信/微信/通知Provider显式development，不读取既有.env/密钥/业务库。临时密码运行时生成，不写产品代码、不打印。
- 容器内存上限合计3.375GiB、CPU配额合计3；另有64MiB合成附件tmpfs卷（uid/gid10001，与应用用户匹配）。额外one-shot迁移最多512MiB，在应用启动前退出。只运行已有Alembic迁移于这个新合成库，不生成新Migration、不迁移持久业务库。
- 最长一次运行30分钟；发布器已有1800秒上限，其余由本次执行者计时收尾。存储卷仅含合成附件；不得用down -v作用于其他项目，不清理镜像或旧卷。

## 执行前门槛与停止条件

确认目标项目/卷/网络均不存在，18093空闲，现有镜像ID与预检一致，至少有4GiB可用内存和合成数据可用空间；任何不满足则暂停此阶段，不复用同名历史资源。配置解析、依赖导入、合成凭证、数据目录写权限和当前源码指纹验证通过后才启动；不把启动失败自动扩大为安装依赖或修改共享服务。

启动前核对应用镜像内置ENV，不允许继承未显式覆盖的生产Provider/外部地址；临时DB/MQ口令使用URL-safe随机字符。Milvus未在本阶段提供，不得因readyz失败擅自连接旧Milvus。独立只读isolated_runtime_plan_review已核对配置与边界，无配置级阻断；config-validation.json仅为CONFIG_ONLY、containers_started=0。

先起三个独立依赖，核对健康；在隔离库运行已有迁移及最小合成组织/房屋/人员/角色数据，再起API/Worker/publisher。不启动Beat、不重放任何既有DLQ。每次模型/短信出口都必须证明为development；发现生产Provider、外网目标、旧库/旧队列或无法确定运行归属立即停止本项目。

## 验收

至少覆盖：住户越权/双角色用途切换；旧附件访问；公告内部字段；主管诊断版本变化；有合格员工普通维修自动派、人工职责/特殊资质拒绝；AI失败仍保留工单及人工队列；接单/处理/回执/住户反馈遵守原容量政策；request/task/analysis关联与可恢复失败。保留原始请求状态码、脱敏响应、队列/数据库记录和源码指纹，负例不能只看页面按钮。

RAG不在本阶段配置真实Milvus/embedding/rerank；不可用分支可验证，但不得把readyz或模型桩当成真实检索质量。微信VM测试与原生工具操作分开，若没有可用原生环境只记录缺口。完成后仅清理具名临时项目资源，保留脱敏证据；旧栈端口、GPU和正式发布仍另行批准。

AGENTS.md第1/8节和用户既定边界要求服务变更单独授权，本阶段未经授权不启动。

## 已获授权后的迁移准备修正

用户已明确授权本阶段。首次配置同库保护、第二次迁移目标保护均真实阻断，并已清理全部临时资源。现复用已有app.cli.delivery_migrate，保留全部guard/版本顺序/bootstrap，不改历史Migration或stamp。临时实例内的库名调整为该入口要求的property_ai_delivery_check；这只是本次新容器内的同名合成库，不是现有delivery实例。

执行前必须核对mysql容器的Compose项目标签为property-ai-business-20261102，唯一网络为该项目verification网络，/var/lib/mysql为本次tmpfs。只有通过这些实际检查，才向一次性迁移容器传入批准标记。迁移容器临时使用staging/关闭dev-auth，数据库URL只交换到这个新实例；API/worker/publisher仍为test、独立队列和development Provider。bootstrap使用运行时生成的合成凭证。现有共享库、网络、数据卷、旧队列均不接入。

这属于已获批“在新隔离库执行已有迁移”的配置修正，未扩大到持久库迁移；所有失败日志保留，授权总时间扣除此前运行耗时，不以每次返修重置预算。

## 扩充同一授权内的场景

在第五次smoke后增加：合成PNG经文件API上传、工单内部现场附件与回执附件区分、关闭工单历史回执可读、员工服务关系撤销后历史上传者拒绝；内部公告经人工审核API发布后住户不可见；WAIT_CONFIRM保持原容量规则及人工职责/特殊技能409拒绝且工单版本/分配不变。人工资格输入为同一临时库中明确标识的合成分析变体（CLEANING/HIGH_VOLTAGE），不是模型能力测试；关系撤销同样只改临时合成人员。累计窗口/网络/镜像/Provider/清理边界不变。新增Q日志在该临时Worker验证，不部署旧服务。

无效输出场景使用已有LLM_PROVIDER_BEHAVIOR=invalid开发桩，仅在同一临时项目；run.py --scenario仅允许success/invalid。未更换真实Provider或对外调用。062416Z已验证人工接管与两轮回执，既有PENDING_ANALYSIS和WAIT_CONFIRM政策均保留。详见本批acceptance.md，仍不是完整发布验收。

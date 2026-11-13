# 旧栈端口收敛：具名维护

用户已开放物业完成所需权限。2026-11-02T12:07/12:14 UTC只读核对仍发现旧property-ai的8个数据/管理端口绑定所有网卡；delivery回环且独立，不在本次变更范围。旧网络实际10个容器，无EKB或其他项目。旧Worker及Day4 Worker当前停止，不能顺带启动。

目标仅 mysql/redis/rabbitmq/minio/milvus 5个容器的HostIp改为127.0.0.1。镜像、完整启动配置、hostname、环境、网络别名及所有数据卷保留；RabbitMQ hostname必须原值，MinIO /data匿名卷及/minio_data卷都必须保留。etcd不重建，但为一致卷备份需在窗口内停止再恢复。源码Compose已回环，不重复修改。

先完成机器可核对的预检：Docker本地Engine当前身份、5个旧容器精确ID/镜像、7个数据卷、10个网络使用者、已有镜像工具、空间和HTTP基线。只读创建请求比较在内存进行，不在报告写环境变量值。不得用带明文password参数的旧mysql_backup/restore脚本；其CLI风险另记，不用于本次维护。

维护顺序：停止原来正在运行的旧/Day4 Nginx与API；停止相关6个数据服务；用既有镜像、只读原卷、无网络、有限内存/CPU备份7个卷到新的忽略目录tmp/runtime-port-maintenance-20261102，逐个tar完整性及SHA核验。备份失败则在尚未改绑定前恢复原运行状态并报告，停止整改步骤。备份不得覆盖，文件不提交、不上传、不输出内容。仅物理卷备份完整性不冒称完整恢复演练。

执行窗口上限30分钟；helper每个256MiB/1CPU、无网络，盘点大小后要求可用空间至少2倍原卷估计量加4GiB且初始至少30GiB。不删除任何旧业务卷。helper清理须同时匹配本次精确名称和owner标签。交换名称前即标记进入不可自动恢复公网绑定阶段，半交换不能误走原栈恢复路径。

独立审查后的执行补强：所有CLI显式绑定本机Linux Engine pipe并清除可能改向其他daemon的环境，CLI/API的Engine ID相同才继续。按保存的容器ID操作；全Engine检查7卷的其他运行写入者，停止后及备份后再次确认无写入者。数据服务须Running=false、ExitCode=0、OOMKilled=false，超时强杀不冒充干净备份。每个操作先写STARTED再记结果；创建超时按本次owner/精确名称核查并移除未接替候选，不删除数据卷。中断也进入恢复路径，helper先停止，再恢复尚未改名的原服务。交换后异常停下受影响消费者/候选/旧依赖，记录精确ID实际状态，禁止同卷双写。

正常操作使用前25分钟，余下5分钟保留恢复/清理；Engine、CLI、健康轮询、HTTP和归档哈希共享deadline。最后报告写入不受单个helper清理失败跳过。端口必须同时满足HostConfig与运行时NetworkSettings.Ports准确8映射，且回环TCP可连；只有配置声明不能通过。已补15个纯合成契约/故障用例，没有用这些测试替代实际停机/备份/运行验收。

仅使用本机Engine复制原配置，补显式匿名卷映射、指定原镜像ID，只改变目标端口HostIp。创建候选后核对Config/HostConfig/卷；原容器改名保留停止，候选接替原名。启动依赖健康后恢复原先运行的消费者；停止状态Worker保持停止，delivery/EKB均不动。源/目标容器全部具名，不使用compose down或全局清理。原容器保留会产生旧Compose标签副本，后续维护必须按精确ID，不能不检查就compose up；最终归档处置另记。

验收：5个容器8端口均实际回环、镜像/启动配置/hostname/全部卷一致；旧/Day4 HTTP对比维护前基线（不把既有Day4停止数据库造成的失败算修好）；delivery容器身份及HTTP不变；无新worker/任务重放/模型调用。备份清单、逐步日志与差异独立只读复审。失败后不自动把端口重新开放所有网卡、不自动恢复业务库；仅保留受影响部分，精确诊断后返修。

不因此宣称本地最新源码已部署。API/Worker混合版本及新源码交付是后续独立步骤，需要迁移差异、统一构建和业务验证。GPU本次约7354MiB占用且有多个进程，未取得独占使用条件；不关闭其他应用、不启动27B推理或重放WO21。

## 首次执行后的限定返修

maintenance-20261102T140746Z 在 Milvus 干净退出断言处中止，未备份、创建或改名，原容器恢复，原正常5个HTTP端点再次200；Day4就绪仍为既有失败。停止约60秒与强杀超时相符，但首次脚本未在断言前落盘ExitCode，恢复后已无法据此认定137；停止窗口日志/事件未取到，不推断为特定上游缺陷。

独立复审建议的最小返修已落实：停止前记录精确ID/StartedAt/RestartCount/信号/时限；正常返回或异常均先记录退出状态再断言，恢复另存HTTP结果。仅Milvus等待180秒，其余60秒；180秒后Docker仍可能强杀，不能称无强杀方案。依然必须Running=false、Restarting=false、OOMKilled=false、ExitCode=0才可备份。20项合成故障/契约测试通过，包括停止响应超时、非零退出证据不会被恢复覆盖。重试若仍失败则保留原运行状态，进一步诊断，不循环强制停止。

上游核对仅用于理解停止机制，不作为本机根因证明：[Docker stop](https://docs.docker.com/reference/cli/docker/container/stop/)、[当前Milvus v2.5.10信号处理源码](https://github.com/milvus-io/milvus/blob/v2.5.10/cmd/roles/roles.go)。不套用其他版本已知故障或升级冻结依赖。

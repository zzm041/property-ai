# T13a / Q：持久任务实际入口关联

隔离运行 run-20261102T051358Z 已通过 28 个 HTTP/业务检查并清理；独立复核发现 execute_intent 直接调用 AnalysisService，N 批 legacy task 日志没有覆盖实际入口。本批落实既定 T13a，不改业务规格。

范围仅 worker/task_intents.py、限定测试入口与专项测试。分析调用记录 request/task/intent/work_order/attempt/analysis_id 与固定结果；执行入口另记录最终持久任务结果，区分 RETRY_PENDING、租约丢失、结果未持久化与业务完成。分析成功不等于派单成功；不把请求重试当成功。来源为已取得的有效租约，日志禁止 token/claim/原文/异常正文。记录失败不得改变异常、租约、重试、业务返回或资源释放。

不增加数据库查询、表、Provider、框架；保留现有事务与恢复分支。测试先 RED 后 GREEN：成功/低置信/无返回/Provider 失败/数据库错误/租约丢失/finish 失败/日志故障，断言实际 durable 分支经过追踪。独立只读复核后，沿用已授权隔离运行窗口和累计时间预算做实际日志关联验证。WIP 用 17/source-before 与保护指纹继承，不提交、不改 EKB。

运行返修：run-20261102T055007Z发现worker实际输出丢失，Celery LoggingProxy子进程复现为0条事件。扩展范围至core/logging.py显式可选输出流及celery_app.py的prefork初始化，Worker使用未被重定向的sys.__stdout__，保留API默认输出与统一脱敏。此为追踪可用性修复，不改变业务配置或引入新日志系统。

# T13a：异步分析任务关联事件

沿用现有结构化日志；仅修改worker/tasks.py的分析任务诊断，不改任务签名、重试预算、数据库或Provider协议。每次进入业务分析记录started，finally记录finished，关联request_id/task_id/work_order_id/attempt/analysis_id及受控结果、耗时。analysis_id仅在实际返回记录后记录；过期任务不写成成功，低置信度不写成自动派单成功。Provider/数据库重试请求不写成已经重试成功。

日志不复制task headers、恢复claim、异常正文、住户原文或模型输出。诊断失败不能替换业务异常或阻止重试。记录是一次任务尝试的观测，不证明Celery实际投递、派单或端到端延迟。

测试：纯mock运行任务成功/低置信度/过期/无返回/Provider错误/数据库重试耗尽/未知错误，核对关联、无敏感正文、重试参数及资源释放；日志抛错仍保留原行为。先RED后GREEN及数据库恢复、失败任务相邻回归，独立只读复审。保护已有WIP，不运行真实队列。

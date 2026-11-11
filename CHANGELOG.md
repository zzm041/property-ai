# CHANGELOG

本文件记录对外可见的版本变化。格式参照 Keep a Changelog，版本号遵循语义化版本。

## v0.1.0 — 2026-11-10

首个可用版本：本地模型驱动的报修受理与规则派单闭环。

### Added（里程碑）

- **本地 27B 模型分析**：工单结构化理解走本机 `qwen38-27b`（Ollama + 本地适配器），提示词版本固定为 `wo-analysis-v26-summary-fidelity`，分析产物落 `work_order_analyses`，模型调用落 `ai_call_logs`（含 `latency_ms`、`attempt`、`model_version`）。
- **规则派单**：能力闸门（职责/技能/在岗/容量）与评分排序在业务服务内执行；`other` 技能为"不设限"，不可派单时返回带 `details.reason` 的 409，而不是裸 409。
- **E/S 双容器交付**：api / worker / publisher / beat 由同一镜像派生，镜像内只保留一份 `app`（`site-packages/app`），`/api/v1/healthz` 回显 `build.app_copies`、契约文件 sha256 与 `source_sha`，容器切换走精确容器程序而非泛化 Compose。
- **2,700+ 单元测试**：`backend/tests/unit` 常态 2,800+ 通过；CI 只跑其中不依赖 MySQL/Redis/模型运行时的稳定子集（52 条），避免把外部依赖带进 CI。
- **多租户与越权边界**：跨租户查询在仓储层用 `tenant_id` 谓词收口，跨租户员工与不存在的员工返回逐字段相同的 409，日志保留区分。

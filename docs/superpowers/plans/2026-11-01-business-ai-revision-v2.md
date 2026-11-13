# 业务闭环与 AI 修订 v2 实施计划

> 主会话按 executing-plans 实施；最终使用独立只读审查。用户明确不允许提交、推送、清理或覆盖 WIP，覆盖技能中默认 commit/清理步骤。用户已要求方案复审后可开始执行，无需再次请求普通修改授权。

**Goal:** 完整保留数轮问题与现实业务纠偏，先完成无迁移、无真实外部调用的确定缺陷修复。
**Architecture:** 原有模块化单体/Worker/Provider。保持现有工单自动化与规则控制，不建设新的支援管理子系统。
**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / 同步 SQLAlchemy；现有其他冻结技术不变。
**Spec:** `docs/superpowers/specs/2026-11-01-business-ai-revision-v2.md`。

## 执行与范围

总台账 T00—T16 见 spec。仅批次 A 达到当前可执行粒度；其他条目仍需各自的接口/策略方案、风险验证或特定授权，禁止从这份总计划直接批量开发。

- 当前工作区 `D:\property-ai`，分支 `codex/real-sms-onboarding`。原地最小修改，不切换分支，不复制遗漏 WIP 的 HEAD 工作树。
- `reports/implementation-review-20261101-01/baseline.json` 保存原 WIP 和索引哈希，四个实现目标的原文副本仅用于本批差异审查。
- 所有测试均使用纯函数、MagicMock 或 httpx.MockTransport，不连接服务、数据库或模型，不读取真实业务数据。
- 不运行全量历史验收。首次观察专项失败，再修改，再运行明确相关回归。
- 环境/迁移/模型分支被授权边界阻挡时，只暂停该分支；不把这些任务标记完成。

## Review Focus

1. 任一未知引用混在正确引用中，不能保留原答案；正整数契约不能接受 bool 或字符串。
2. 重排超时、限流和格式错误保持受 ACL 限制的原召回顺序；不虚构相关性分数，也不声称已解决弱证据质量。
3. unavailable 与 insufficient 状态不能混淆；上游失败不能继续生成。
4. 置信度为 0 的配置在新分析、存量结果重映射两条路径均生效；保留未知类别保护。
5. 批次不得改动现有 WIP、运行服务或冻结 EKB；评审检查实际差异而非执行者报告。

## Task A1：RAG 异常、重排与引用输出契约

**修改：** `backend/app/service/retrieval.py`、`backend/app/provider/reranker/__init__.py`、`backend/app/domain/knowledge.py`。
**新增测试：** `backend/tests/unit/test_retrieval_failure_contract.py`、`backend/tests/unit/test_reranker_output_contract.py`。
**接口：** 保持 `RetrievalService.query(...) -> AssistantAnswer`、`HttpRerankerProvider.rerank(...) -> tuple[float, ...]`；`RagAnswerOutput.citation_chunk_ids` 改为正 StrictInt 元组。

- [x] 用注入 Provider 的查询测试复现超时、限流、坏分数、未知引用、错误 unavailable 状态。断言最终状态、空答案/引用、后续调用次数和排序，非源码字符串测试。
- [x] 用 MockTransport 验证根列表/null、长度不符、bool、None、NaN/Infinity、非法数字字符串被映射为 ProviderInvalidOutputError；有限分数及现有 results 字典形式继续可用。
- [x] 执行新测试并保存 RED 日志，预期有真实断言失败。
- [x] 最小实现：已知 ProviderError 家族处理、有限分数校验、无分数的原顺序回退、严格引用集合校验与状态顺序。
- [x] 执行新测试及 day2 Providers、knowledge policy/scope、local AI HTTP 的 RAG prompt 相关测试，预期零失败。保存 GREEN 与相关回归日志。

## Task A2：置信度零值配置

**修改：** `backend/app/service/analysis.py`。
**新增测试：** `backend/tests/unit/test_analysis_threshold_contract.py`。
**接口：** `_validate_payload(...)` 与 `_mapped_from_stored_analysis(...)` 接口不变。

- [x] 对两个接口分别参数化 0.0/0.2/0.7/1.0，比较 confidence 与 threshold，包含相等边界及 OTHER 未知类别保护。
- [x] 在当前代码上运行测试，保存零值错误的 RED 证据。
- [x] 去掉 `or DEFAULT_CONFIDENCE_THRESHOLD`，直接传入 Settings 的已验证数值；删除仅因此无用的 import，不扩大重构。
- [x] 运行新测试及 analysis input/crossfield/observed event/auto dispatch/maintenance dispatch，预期零失败。

## Task A3：审查与交付

- [x] 核对所有覆盖台账项都有处置；未实施项继续 OPEN，不将规划表全部打勾。
- [x] 检查本批 diff、空白错误和 WIP/index 哈希；源码目标必须与 baseline 初始内容比较，不混入其他 WIP。
- [x] 独立只读审查本批实际代码、测试、设计及日志；不得递归创建审查者，不得改文件。
- [x] 发现 Important/Critical 问题则修正并复测；若与授权边界有关，只暂停相关部分。
- [x] 保存主会话审查、独立审查结论及剩余事项；最终只声明实际验证范围。

## 下一批入口（本批不直接实施）

按 T04/T05 权限负例、T03 连接与时效、T06b 复用指纹继续做具名风险验证，再冻结最小修正子计划。T07/T08/T10 先复用现有进展/决定/回执，核对是否确需新字段；新持久字段和迁移单独列审批。T09 先比较策略，T11—T14 在明确业务与模型预算后推进。T01 任何服务变更先列对象/回退，EKB 继续只读。

## 本批执行结果

A1/A2/A3 已完成：73 项专项、355 项相关回归通过；独立复审问题已返修关闭。具体失败与通过日志、保护哈希、独立复审及剩余 OPEN 事项见 `reports/implementation-review-20261101-01/`。上方完成勾选仅限本批任务，不代表 T00—T16 全部功能完成。没有全量历史验收、服务变更或真实推理。

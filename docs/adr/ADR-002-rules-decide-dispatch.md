# ADR-002：派单选人由规则决定，AI 只负责填字段

- 状态：已采纳
- 日期：2026-11-11（记录既有实现）
- 相关：`docs/05-AI-DISPATCH-SPEC.md`、`docs/04-WORKORDER-STATE-MACHINE.md`

## 背景

工单派给谁，是有责任后果的业务决定：派错人 → 延误、返工、业主投诉；派给不合格的人 →
安全风险（电、气、水）。模型输出不稳定（同一条文本两次给出不同 `required_skill`），
一旦让模型直接点名员工，出错时既不能解释、也不能复现。

原话在 `AGENTS.md:63`：「派单禁止让 LLM 直接点名员工；必须规则过滤 + 评分。」

## 决策

1. AI 的输出被**降级为字段**：`category / subcategory / severity / priority / risk /
   required_role / required_skill / confidence / prompt_version`。落库前统一过契约
   （`domain/ai.py:279 map_analysis_output`、`:146 normalize_skill`）。
2. 选人完全由规则算：先硬过滤（`domain/ai.py:407 hard_filter_candidate`，返回
   `FilterReason:43` 之一），再算分（`:439 score_candidate`，权重在 `:362 DispatchWeights`），
   最后排序（`:500 rank_candidates`）。
3. 硬过滤的原因必须可解释、可落库：`NOT_ACTIVE / USER_NOT_ACTIVE / PROPERTY_NOT_EFFECTIVE /
   NOT_CHECKED_IN / NOT_ON_DUTY / DUTY_MISMATCH / SKILL_MISMATCH / CAPACITY_FULL /
   NO_STAFF_ROLE / RECENTLY_REJECTED / BUSY`（`domain/ai.py:43-53`）。对外 409 带
   `details.reason`，不返回裸错误。
4. 人工指派走同一套校验（`service/work_order.py` 的资格检查），不因为「是管理员」就跳过。
5. 600 秒内被拒过的员工不再自动派：`service/auto_dispatch.py:678
   _recently_rejected_employee_ids`，命中即 `RECENTLY_REJECTED`（`:359`）。

## 替代方案

| 方案 | 未采纳原因 |
| --- | --- |
| LLM 直接输出员工 id | 不可复现、不可解释；员工增删后模型会点不存在的人 |
| 纯加权排序（无硬过滤） | 会让「未签到 / 技能不符」的人进候选池，安全风险不可接受 |
| 全自动派单，无人确认 | 拒单、外协、返工都没有出口；实测 600s 冷却与 BUSY 需要人工介入 |
| 只靠人工指派 | 单量大时管理员成为瓶颈，且没有推荐依据 |

## 后果

- 正向：任何一次派单都能回答「为什么是他」——`dispatch_recommendations` 里有分数、
  `work_order_logs` 里有 `AUTO_DISPATCH_WAIT`（含 `reason_code` 与 `policy_version`），
  409 响应里有 `reason`。
- 正向：AI 挂掉不阻塞业务：工单照常创建，落 `PENDING_ANALYSIS`，进入人工队列
  （`AGENTS.md:62`）。
- 代价：词表忠实度不足时分析会落到 `AI.INVALID_OUTPUT`（`label_not_matched`），需要重试或
  人工补录——这是「宁可拒绝也不猜」的代价，成本在可解释性这一侧被换回来了。
- 代价：规则表（技能、值班、容量、冷却）本身要维护；改规则要重跑日派单回归。
## 证据（文件:行号）

- `AGENTS.md:62`、`AGENTS.md:63`
- `backend/app/domain/ai.py:43`（`FilterReason`）、`:146`（`normalize_skill`）、`:165`
  （`ORDINARY_MAINTENANCE_SKILLS`）、`:279`（`map_analysis_output`）、`:385`
  （`capability_filter_reason`）、`:407`、`:439`、`:500`
- `backend/app/service/auto_dispatch.py:116`（`maybe_assign`）、`:210`（`_record_wait`）、
  `:269`（`_evaluate_live_candidates`）、`:359`、`:431`（`_commit_auto_assignment`）、`:678`
- `backend/app/model/ai.py:138`（`DispatchRecommendation`）
- 运行时观测：`GET /api/v1/supervisor/work-orders/{id}/diagnosis` 的 `facts` 含
  `WAITING_IDLE` 等结论（`backend/app/api/v1/supervisor_work_orders.py:105`）

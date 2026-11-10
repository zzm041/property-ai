# PA-167 — analysis.required_skill 未归一化（electrician vs ELECTRICAL）

## 结论

`analysis.required_skill` 落库的口径和派单读取的口径不一致。修复点不在派单侧（派单一直是对的），
而在 `AnalysisService._fill_analysis`：它把模型原话写了进去。

```python
# backend/app/service/analysis.py（改前）
# Keep the original model skill, not the derived execution skill. Stored
# analysis is remapped through the same contract for subsequent dispatch.
analysis.required_skill = mapped.raw_required_skill or mapped.required_skill
```

`mapped.raw_required_skill` 是模型的原始 label（`electrician` / `maintenance`），
`mapped.required_skill` 才是派单比较的技能码（`ELECTRICAL` / `MAINTENANCE`）。
`map_analysis_output` 早就归一化了（`backend/app/domain/ai.py:272`），但只在内存对象上，
落库那一行又被换回原话。

影响面：派单不受影响（`_mapped_from_stored_analysis` 会重新过一遍 `map_analysis_output`），
受影响的是**直接读这一列的消费者**——`GET /supervisor/work-orders/{id}/analysis`
（`backend/app/api/v1/supervisor_ai.py:44` 直接透传 `record.required_skill`）、
管理端展示、以及运维/报表 SQL。同一张工单，接口上写 `maintenance`，派单日志里写 `MAINTENANCE`。

## 复现（改前）

集成用例直接断言落库值 `backend/tests/integration/test_ai_runtime.py:123`：

```python
assert analysis.required_skill == "ELECTRICAL"
```

改前跑（`D:\AI\runtime\t109\ai-runtime-before.log`）：

```
E       AssertionError: assert 'electrician' == 'ELECTRICAL'
E         - ELECTRICAL
E         + electrician
```

`electrician` 来自开发用伪 LLM（`backend/app/provider/llm/__init__.py:29` `"required_skill": "electrician"`），
和本地 27B 返回 `plumber` / `maintenance` 是同一类原话。

交付库实测（`property-ai-delivery-check-mysql-1` / `property_ai_delivery_check`，改前）：

```
required_skill  rows_count
NULL            117
maintenance      30
cleaning          2
```

149 条分析里 32 条是模型原话，0 条是技能码。

## 修复

1. `backend/app/domain/ai.py`
   - 新增 `normalize_skill(raw) -> (skill_code, resolution)`，三态显式返回：
     `aliased`（`electrician` → `ELECTRICAL`）、`canonical`（`ELECTRICAL` 原样）、
     `unmapped`（没有任何别名的值，例如 `HAZMAT_LICENSE`）。
   - `MappedAnalysis` 增加 `skill_resolution` 字段；`map_analysis_output` 改用它，
     不再靠「字符串有没有变」反推。
   - 未知值**不**降级成 `OTHER`：`OTHER` 是「工种未定」的放行标记，把未知技能改成它等于
     悄悄打开派单闸门（PA-101-A 已把这条写进契约）。未知值继续作为硬约束，但必须被点名。
2. `backend/app/service/analysis.py`
   - `_fill_analysis` 落库改为 `analysis.required_skill = mapped.required_skill`（技能码）。
     原始 label 不丢：`MappedAnalysis.raw_required_skill` + 分析审计事件里的
     `raw_required_skill` 仍然保留。
   - `_validate_payload` 遇到 `unmapped` 时打一条 `analysis_skill_not_normalized`
     WARNING，带 `reason_code=SKILL_VALUE_NOT_NORMALIZED`、原始 label 和落库值。
   - `_append_success_audit` 增加 `skill_resolution`；`backend/app/domain/audit.py`
     的 `WORK_ORDER_AI_ANALYSIS_COMPLETED` 白名单同步加键。
3. 落库值现在是幂等的：`_mapped_from_stored_analysis` 重新映射一行技能码，结果不变
   （`skill_resolution` 变 `canonical`），所以改前写入的行、改后重新派单都不会漂。

## 回归

`backend/tests/unit/test_pa167_skill_normalisation.py`（18 例）：别名 / 大小写 / 未知值三态、
未知值不变成 `OTHER`、落库是技能码、落库行再映射不变、审计同时记录两种口径、
未知值打 WARNING 且分析仍然成功（不静默、也不误判成失败）。

`backend/tests/unit/test_analysis_crossfield_contract.py::test_stored_original_roundtrip_uses_same_mapper_for_recommendation_and_redispatch`
原先断言 `stored.required_skill == 'maintenance'`（即把原话落库当成契约），改为断言
`stored.required_skill == 'CLEANING'` 且再映射不变。

跑出来的数：

| 范围 | 改前 | 改后 |
| --- | --- | --- |
| `backend/tests/integration/test_ai_runtime.py` | 5 failed / 9 passed / 1 error | 2 failed / 13 passed |
| `backend/tests/unit`（全量） | 40 failed / 2914 passed / 14 skipped | 40 failed / 2932 passed / 14 skipped |
| `test_pa167_skill_normalisation.py` + `test_analysis_crossfield_contract.py` | — | 35 passed |

剩下 2 条与本次无关（改前也是红的）：

- `test_stale_result_does_not_override_human_cancel`：对已 CANCELLED 的工单调用分析返回
  `None`，用例期待一条 `STALE` 的分析行——是「取消后是否还建分析记录」的契约问题。
- `test_human_override_records_recommendation_difference`：人工指派被
  `Employee is not eligible for dispatch` 拦，属于 fixture 的签到/容量前提，不是技能口径。

## 存量脏值（一次性修正 SQL）

`reports/pa167/normalize-analysis-skill.sql`（修）与 `…rollback.sql`（回滚）。

执行前先备份到 `pa167_analysis_skill_backup`，修完 `known_labels_left = 0`；
回滚从备份表还原（原 label 无法从修好的值反推，所以备份表是回滚的唯一来源）。

交付库预演（`START TRANSACTION` + 同样的 UPDATE + `ROLLBACK`，不使用 DDL）：

```
updated_inside_txn = 32
inside txn:  NULL 117 / MAINTENANCE 30 / CLEANING 2
after ROLLBACK: NULL 117 / maintenance 30 / cleaning 2     ← 数据未被改动
```

即：这次修数据会改 32 行（30 `maintenance` + 2 `cleaning`），可回滚。

> 记录一次操作失误：第一次预演把建辅助表的 DDL 放进了事务里，MySQL 在 DDL 前后隐式提交，
> 于是 UPDATE 真的落库、`ROLLBACK` 没有回滚。发现后立刻按原 label 还原（`LOWER()` 仅作用于
> 这两个值，行数与改前逐项一致：`maintenance` 30 / `cleaning` 2 / `NULL` 117），
> 并在 SQL 文件头部写明「不要用 START TRANSACTION 包这个文件」，另给了不用 DDL 的可回滚预演写法。

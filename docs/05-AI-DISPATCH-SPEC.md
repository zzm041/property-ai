# 05 — AI 理解与智能派单规格

## 1. 原则

- AI 输出建议，规则引擎与主管拥有最终控制权。
- LLM **不得**直接从员工姓名列表选择执行人。
- 任何模型调用都要有 timeout、有限 retry、结构化校验、fallback、日志。
- AI 全部失败时：工单已创建，状态 `PENDING_ANALYSIS`，进入人工队列。

## 2. AI Gateway

统一入口 `AiGateway`（service），内部调用 Provider：

| Provider | 用途 | Dev 行为 |
| --- | --- | --- |
| `LLMProvider` | 文本理解、摘要、公告草稿、知识问答 | 返回固定 schema 的样例 JSON |
| `VisionProvider` | 现场图/报修图分类与描述 | 返回低置信度样例，便于测人工队列 |
| `ASRProvider` | 语音转文字 | 可返回预设文本或“转写失败” |
| `EmbeddingProvider` | bge-m3 向量 | 本地可切换 mock / 远程 |

Production 对接 Qwen-family LLM / VLM 与选定 ASR。密钥只在环境变量。

### 2.1 调用日志 `AiCallLog`

必记：request_id, tenant_id, biz_type, prompt_version, model_version, latency_ms, token_usage, retry_count, success, error_code, input_hash（不明文存完整身份证/手机）。

### 2.2 可靠性

| 项 | V1 要求 |
| --- | --- |
| timeout | LLM 8s，VLM 12s，ASR 20s（可配置，禁止写死在业务 if 里） |
| retry | 仅对超时与 5xx，最多 2 次，指数退避 |
| validation | Pydantic 校验 Structured Output，失败算模型失败 |
| fallback | 文本：规则关键词粗分类；图像：跳过视觉；语音：保留文件 |
| 低置信度 | `confidence < 阈值` → 人工分析，不自动派单 |

## 3. 工单理解 Structured Output

```json
{
  "category": "water_supply",
  "subcategory": "no_water",
  "severity": "high",
  "priority": "p2",
  "summary": "本栋突然停水",
  "risk": "none|safety|flooding|electric|gas|other",
  "required_role": "STAFF",
  "required_skill": "plumber",
  "confidence": 0.86
}
```

字段全部枚举化。未知类型用 `category=other`，强制人工。  
Gateway 只返回该对象或明确失败，不返回自由散文作为控制信号。

输入上下文：报修文本、ASR 文本、图像理解摘要、房屋（小区/楼栋/单元/房号）、报修人关系类型、近期同房工单摘要（有限条）。

## 4. 派单流水线（强制顺序）

```text
AI/人工得到 category, required_duty/skill, severity
        ↓
规则引擎校验（类型是否启用、是否危险工单需双人等 V1 可先做开关）
        ↓
读取工单所属 property 当天人员状态
        ↓
硬过滤（任一不满足即剔除）
        ↓
剩余候选人评分
        ↓
自动派单 或 无候选 → 待人工派单
```

### 4.1 硬过滤

必须全部通过：

1. Property 业务时区的 `business_date` 当天已签到
2. 当天 `EmployeeDailyStatus` 为在岗（非请假/离岗）
3. 目标时点存在覆盖本小区的有效 `EmployeeProperty`（`status=ACTIVE`，`valid_from` 已生效，`valid_to` 为空或尚未失效）
4. 职责匹配（`required_duty`；若 AI 只给 skill 则 skill 匹配）
5. 技能匹配（有 required_skill 时）
6. `current_load < max_capacity`
7. 员工 User 状态正常、角色含 STAFF 或以上

一个 Employee 可有多个有效 `EmployeeProperty`，但候选筛选必须针对工单所属 Property。**禁止**把未签到、关系已失效或仅属于其他 Property 的人员因“历史上是维修工”而派单。

### 4.2 评分（可配置权重，默认如下）

| 因子 | 默认权重 | 方向 |
| --- | --- | --- |
| 职责精确匹配 | 0.25 | 高更好 |
| 技能匹配 | 0.20 | 高更好 |
| 负载余量 `(max-load)/max` | 0.20 | 高更好 |
| 服务区域（同楼栋历史） | 0.10 | 高更好 |
| 平均响应（倒数为分） | 0.10 | 快更好 |
| 完成率 | 0.08 | 高更好 |
| 用户评价 | 0.07 | 高更好 |

得分最高者派单。并列时选负载更低，再并列选最近签到。  
分数与过滤快照写入 Assignment.payload，便于审计“为什么派给这个人”。

### 4.3 人工通道

- 人工派单 / 改派：主管选择员工，仍做硬过滤，除非 `force=true` 并写审计（例如紧急叫回未签到人员，V1 允许强制但必须原因）。
- 拒单：写原因，工单回 `PENDING_ASSIGNMENT`，原员工短时降权（可配置冷却）。
- 超时未接单：自动改派下一名，写日志。
- 管理员接管：生成新 Assignment。

## 5. 集中事件识别

Worker 周期或工单创建后触发：

- 窗口：默认 10 分钟（可配）
- 维度：同一 `property_id` + 同一 `building_id`（或配置到 unit）+ 同一 `category/subcategory`
- 阈值：默认 5 单（可配）

命中则创建或并入 `Incident`，成员工单挂 `incident_id`。  
派单：Incident 若已有主处理人且在岗，新成员工单默认跟派，不再每人派一个新员工。

AI 对 Incident：汇总、运营摘要、公告草稿。草稿进入公告审核流。 **禁止自动发布。**

## 6. 知识助手

- 检索：bge-m3 embedding → Milvus，filter `tenant_id` AND (`property_id` = 当前小区 OR 企业通用) AND visibility 对角色合法。
- 生成：Qwen LLM，提示词要求只根据检索片段回答，并输出引用列表（文档 id、标题、片段）。
- 无命中：明确说不知道，引导转人工/报修，禁止编造制度条款。

## 7. 失败即降级矩阵

| 失败 | 工单 | 派单 | 用户感知 |
| --- | --- | --- | --- |
| ASR 失败 | 创建成功 | 不自动派 | 提示语音待人工，可补文字 |
| VLM 失败 | 创建成功 | 仅文本理解 | 图片已保存 |
| LLM 失败/非 JSON | PENDING_ANALYSIS | 否 | “已收到，正在安排” |
| 低置信度 | PENDING_ANALYSIS | 否 | 同上 |
| 无在岗匹配员工 | PENDING_ASSIGNMENT | 否 | 已受理，等待安排 |
| RAG 失败 | 不影响工单 | — | 助手暂时不可用 |

## 8. 提示词与版本

提示词文件纳入版本库，带 `prompt_version`。变更必须可回滚，并在 AiCallLog 记录。禁止把提示词只放在运营同学聊天记录里。

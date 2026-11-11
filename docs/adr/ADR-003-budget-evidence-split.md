# ADR-003：预算计数与评审证据拆账（PA-166）

- 状态：已采纳（2026-11-11 落地，commit `2fa1449`）
- 相关：`docs/09-DEPLOYMENT.md:182`（预算与评审证据小节）

## 背景

适配器把每一次推理调用写成 `private/calls/call-NNN/`（`reservation.json` + `result.json`），
预算按「`call_baseline` + 已计数调用数 < `limits`」判定（`LIMITS` = asr 6 / embedding 12 /
reranker 8 / qwen 120）。同一目录里还躺着**评审证据**：`call-001`、`call-008` 等被
`resolutions[]` 复核过的资产，launcher 又要求它们必须存在（缺了 `up` 直接拒绝启动）。

于是出现一个死结：**恢复证据 = 打爆预算**。t114 实测 `limits.qwen=105`、
`call_baseline.qwen=46`、`calls/` 里 66 个目录（其中 63 个是历史证据）⇒
`46 + 63 = 109 ≥ 105`，任何一次真实调用都 `R2_CALL_LIMIT`，对外恒为
`MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY`，`detail_code` 全为 `null`，分不清「没额度」还是
「配置错」。当时的临时解是把 `limits.qwen` 抬到 120、`call_baseline` 归零——那是改尺子，不是修问题。

## 决策

1. 预算只计**未被声明为证据**的调用；证据目录单独计数、单独归档，不参与 `limits` 判定。
2. 证据通过两处声明：配置键 `evidence_records`（显式目录名列表），以及 `resolutions[].record`
   自动计入（避免同一份证据要在两个地方各写一遍）。
3. 判定口径不变：完成屏障 `gate()` 仍遍历**全部**调用（含证据），证据缺失/漂移依然
   fail-closed；失败输入不自动重放、窗口耗尽的语义都不动。
4. 可观测：`/healthz` 的 `budget` 回显 `limits / call_baseline / counted / counted_records /
   evidence_excluded / evidence_records`；每次 `reservation.json` 记 `budget_counted: true` 与
   `evidence_excluded_before`，事后可复算。
5. 配置回退：临时改过的 `limits.qwen=120` / `call_baseline.qwen=0` 恢复为 105 / 46，
   `evidence_records` 补 66 条（2026-11-11T20:36:27Z）。`up` 会整块重写 `config.json`，
   所以清单改为文件 `tmp/delivery-work-ai-runtime/evidence-records.txt`，由 `up` 读入。

## 替代方案

| 方案 | 未采纳原因 |
| --- | --- |
| 继续抬 `limits`（105→120→…） | 上限常量 `LIMITS` 就是 120，抬到 121 直接 `INVALID_LIMITS`；即便能抬也只是把天花板往上挪，证据一多又会撞 |
| 把证据挪出 `calls/` | launcher 要求 `calls/call-001` 等存在才能启动（评审证据是启动前提），挪走即拒启动 |
| 证据目录改名（`evidence-001`） | 现有 `resolutions[].record`、`resolution_root` 的 sha 校验全指向 `call-NNN`，改名等于作废历史评审 |
| 只做文档说明，不改代码 | 线上仍会把证据当花费，任何人恢复备份就再次踩死 |

## 后果

- 正向：真实证据集（66 条证据 + 1 次真实调用）验算：旧口径 `46+63=109` 拒；新口径
  `46+1=47`，余量 58。单入口 `up` 后实跑：`evidence_excluded=63`、`counted={asr:0,qwen:4}`、
  `counted_records=[call-064…067]`，`46+4=50/105`。
- 正向：预算语义变清楚——`counted_records` 直接列出「这窗口里花掉钱的是哪几次」。
- 代价：证据集合成为配置的一部分，漏声明会让历史证据重新计入；`up` 重写配置使手工改动
  静默失效（因此走清单文件）。
- 代价：新增 12 条单测（`backend/tests/unit/test_pa166_budget_split.py`）守着「证据不占额度 /
  真实调用扣额度」两个方向。
## 证据（文件:行号）

- `backend/app/provider/local_ai/runtime_control.py:437`（读 `evidence_records`）、`:441`
  （并入 `resolutions[].record`）、`:593`（`reservation_paths`）、`:595`（`budget_ledger`）、
  `:604`（分桶判定）、`:606`（`budget_usage`）、`:615`（回显证据名）
- `backend/app/provider/local_ai/http_api.py:47`（`/healthz` 带 `budget`）
- `backend/app/provider/local_ai/runtime_control.py:143`（`LIMITS` 上限）、`:640`（`reserve`）
- `docs/09-DEPLOYMENT.md:182-199`（拆账表、清单文件、判定不变项）
- `backend/tests/unit/test_pa166_budget_split.py`（12 例）

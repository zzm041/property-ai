# ADR-001：27B 本地模型 + 受审适配器，而不是直连云 API

- 状态：已采纳
- 日期：2026-11-11（记录既有实现，非新增决策）
- 相关：`docs/09-DEPLOYMENT.md:182-243`（预算拆账 / 单一入口 / 资源地板）

## 背景

业务方对数据出域敏感：工单文本、业主联系方式、室内照片、语音都属于不能随意转发给第三方托管
推理的数据。同时 V1 需要一条能真跑的能力链（文本分析、图片 VLM、语音 ASR、知识库检索），
而不是「开发期用 mock、上线再换」。

原话落在 `AGENTS.md:90`：「AI 默认部署：采用许可允许的开放权重 Qwen 等模型，在我们或企业控制的
服务器上自托管…未批准不得租机、购买硬件、开启计费实例或向第三方模型平台转发业务数据。」

## 决策

1. 推理跑在自托管 Ollama（默认 `qwen38-27b-q3km-g55:7af3b8f`，27B q3），业务后端只认
   OpenAI-compatible HTTP（`host.docker.internal:11437/v1`），不认任何公网模型端点。
2. 在模型与后端之间加一层**受审适配器**（`backend/app/provider/local_ai/`）：它持有预算账本、
   完成屏障、评审证据校验与资源地板，是唯一允许触碰模型进程的组件。
3. 适配器只由 `deploy/ai-runtime.ps1 up|down|status|verify|resolve-unknown` 拉起；进程、
   源码提交、adapter sha256 三者写进 receipt，供事后对账。
4. 后端凭证只允许「本地端点 + 专属 token」组合：`LOCAL_AI_ENDPOINT_HOSTS`
   （`backend/app/core/config.py:19`）限定主机集合，非本地端点共享 token 一律拒绝
   （`config.py:377 _reject_shared_token_off_host`）。

## 替代方案

| 方案 | 未采纳原因 |
| --- | --- |
| 直连云厂商 API | 数据出域；且无法用预算/证据闸约束「谁花了第 105 次调用」 |
| 自托管但后端直连 Ollama | 失去预算、完成屏障、证据校验、资源地板四道闸；模型被并发打穿时没有任何观测点 |
| 用 4B 小模型换取速度 | 词表忠实度与九字段稳定性更差，且改变对外承诺的模型口径，须单独评审（`docs/09-DEPLOYMENT.md:232`） |
| 开发期只用 mock provider | 图片/语音链路从未真跑过，交付时无法证明能力存在 |

## 后果

- 正向：模型、数据、调用记录全在本地；每次调用都有 `call-NNN/reservation.json` 与
  `result.json`；预算耗尽、模型未常驻、显存不足都能在适配器侧定位（`/healthz` 的
  `budget` / `floors` / `resource_floor`）。
- 代价：单机 27B 串行（`context_length=8192`），32GB/16GB 机器上「模型常驻 + 视觉」正好顶穿地板
  ——实测空闲显存 1941–1962 MiB vs 地板 2000、宿主可用 4417–4777 MiB vs 4500
  （`docs/09-DEPLOYMENT.md:228`）。这是容量问题，表现为 `RESOURCE_ADMISSION_REJECTED`。
- 代价：改一行适配器代码就会让 `up` 因钉值不符而拒绝启动（`deploy/work-ai-runtime.ps1:20`
  `$SourceCommit`、`:25 $ExpectedAdapterSha256`）——这是刻意的：不允许「跑的代码说不清是哪份」。

## 证据（文件:行号）

- `AGENTS.md:90`（自托管口径）、`AGENTS.md:62`（AI 无最终业务控制权）
- `backend/app/core/config.py:19`、`:377`、`:479`（`ai_credentials()`）、`:527`（`media_pipeline()`）
- `backend/app/provider/local_ai/runtime_control.py:143`（`LIMITS`）、`:623`（`gate()`）、`:640`（`reserve()`）
- `backend/app/provider/local_ai/http_api.py:47`（`/healthz` 与 `/readyz` 同载荷）
- `deploy/ai-runtime.ps1:3`（唯一入口的 `ValidateSet`）、`deploy/work-ai-runtime.ps1:20/25/26`（另可走 `--ArtifactManifest` 由 `scripts/ops/verify_runtime_artifact.py` 校验，见 `:66-77`）
- `docs/09-DEPLOYMENT.md:199`（单一入口与对账三步）、`:211`（资源地板可观测可覆盖）

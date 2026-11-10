# PA-117 修前复现与修复记录（property-dev / t30，2026-11-10）

对象：staging 下 `LLM_PROVIDER=production` 而 `LLM_API_KEY` 为空时，服务**照常 ready**，
直到真正调用 AI 才抛 `ProviderUnavailableError`。

## 1. 修前复现（api 容器实测）

容器：`acaf86ec88a0_property-ai-delivery-check-api-1`（镜像 `ad53d436653a`，`APP_ENV=staging`）。

### 1.1 凭证状态（只打印长度，不打印值）

```bash
docker inspect "$API" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | awk -F= '/^(APP_ENV|LLM_PROVIDER|LLM_API_KEY|LOCAL_AI_INTERNAL_TOKEN)=/{printf "%s len=%d\n",$1,length($2)}' | sort
```

```
APP_ENV len=7                      # staging
LLM_API_KEY len=0                  # ← 空
LLM_PROVIDER len=10                # production
LOCAL_AI_INTERNAL_TOKEN len=43     # 有共享 token
```

### 1.2 服务仍然 ready

```bash
curl -s -o readyz.json -w 'http=%{http_code} total=%{time_total}s\n' http://127.0.0.1:8093/api/v1/readyz
```

```
http=200 total=0.253709s
data.status = ready
data.dependencies.llm = {"status":"not_checked","critical":false,"reason":"live_vendor_not_probed"}
```

### 1.3 只有在调 AI 时才失败

`D:\AI\runtime\pa117-repro.py`（`docker cp` 进 `/tmp` 后 `docker exec ... python /tmp/pa117-repro.py`）：

```json
=== 1. process credential state (lengths only) ===
{"APP_ENV":"staging","LLM_BASE_URL":"http://host.docker.internal:11437/v1","LLM_PROVIDER":"production",
 "llm_api_key":{"length":0,"set":false},"local_ai_internal_token":{"length":43,"set":true},
 "settings.llm_api_key":{"length":0,"set":false}}
=== 2. build_llm_provider() wiring ===
{"model":"qwen38-27b-q3km-g55:7af3b8f","provider.api_key_length":0,"provider.api_key_set":false,
 "provider_class":"QwenLLMProvider"}
=== 3. one work-order completion call ===
{"elapsed_ms":0.0,"error_code":"AI.UNAVAILABLE","error_type":"ProviderUnavailableError",
 "message":"LLM is unavailable","outcome":"failed"}
exit=1
```

`elapsed_ms` 是 0.0 而不是台账记的 380ms，原因是失败点在 `QwenLLMProvider.structured_completion`
的第一行：`if not self.base_url or not self._api_key: raise ProviderUnavailableError`。空 key 不发出任何
网络请求，所以比"请求被拒"更快。故障性质与台账一致，只是比记录的更早短路。

### 1.4 为什么本地 token 救不回来（根因补充）

`backend/app/provider/factory.py`：

- `build_llm_provider`（工单分析）→ `api_key=settings.llm_api_key.get_secret_value()`，**没有兜底**；
- `build_rag_llm_provider` / `build_vlm_provider` / ASR（同文件 :94、:145、:162）→ 都走 `settings.resolve_local_ai_key(...)`。

即：RAG/VLM/ASR 三条路允许回落到 `LOCAL_AI_INTERNAL_TOKEN`，唯独工单分析这条路不允许。
容器里 `local_ai_internal_token len=43` 却依然拿到空 bearer，就是这条不一致造成的。
本次按"最小改动、不夹带重构"没有改 factory 的取 key 逻辑，只在配置层把事实暴露出来
（`ai_credentials.fallback_to_local_token=false`）。

## 2. 修复

### 2.1 `backend/app/core/config.py`

1. 新增 `Settings.ai_credentials()`：返回 `{provider, status, missing, sources, fallback_to_local_token}`，
   `status ∈ {not_required, configured, missing}`，**不含任何密钥值**。
2. `validate_security_configuration` 增加 staging 分支：`app_env=staging ∧ llm_provider=production ∧
   LLM_API_KEY 与 LOCAL_AI_INTERNAL_TOKEN 都为空` → 启动即 `ValueError`。

### 2.2 `backend/app/core/readiness.py`

`check_readiness()` 返回值新增 `ai_credentials`（`settings.ai_credentials()`）。
整体 `status` 与 HTTP 码**不改**：api/beat/publisher 从不调用模型，把它们的就绪度降级是误报。

### 2.3 为什么选"暴露"而不是"一律启动即失败"

`Settings` 被 api / worker / beat / publisher 四个角色共用，只有 worker 真正调模型。
若 staging 下"`LLM_API_KEY` 为空即拒绝启动"，本次这台 api（`LOCAL_AI_INTERNAL_TOKEN` 有值、
从不调模型）会直接崩溃重启——把"一个角色缺凭证"变成"整个交付栈起不来"，比原来的静默更糟。
因此拆成两档：

| 情形 | 处理 | 理由 |
|---|---|---|
| 两条凭证都为空 | **启动即失败** | 任何角色都不可能工作，属于确定的部署错误 |
| 仅 `LLM_API_KEY` 为空（本地 token 在） | **`/readyz` 暴露 `ai_credentials=missing`** | api/beat/publisher 不需要它；worker 需要，运维能一眼看到 |

## 3. 修复后实测（同一容器，注入两个模块后重启）

```json
data.status           = ready
data.ai_credentials   = {"fallback_to_local_token": false,
                         "missing": ["LLM_API_KEY"],
                         "provider": "production",
                         "sources": {"llm_api_key": "missing", "local_ai_internal_token": "configured"},
                         "status": "missing"}
```

配置齐备时（`docker exec -e LLM_API_KEY=dummy-key-for-shape-check`）：

```json
{"fallback_to_local_token": false, "missing": [], "provider": "production",
 "sources": {"llm_api_key": "configured", "local_ai_internal_token": "configured"},
 "status": "configured"}
```

两条都空时（`docker exec -e LLM_API_KEY= -e LOCAL_AI_INTERNAL_TOKEN=`）：

```
ValidationError: 1 validation error for Settings
  Value error, Staging LLM provider requires LLM_API_KEY or LOCAL_AI_INTERNAL_TOKEN
```

复现脚本：`D:\AI\runtime\pa117-repro.py`；容器原始模块备份：`D:\AI\runtime\pa117-container-backup\`。
验证完已按原字节回滚（`md5 3d09353ad2a8802a465d9264c74126ac` / `182ce5e88ad7a7b4281c96807d4bcb19`）
并重启，`readyz` 回到无 `ai_credentials` 字段的旧形态，容器 `healthy`。

## 4. 回归

```bash
cd D:\property-ai\backend
../.venv/Scripts/python.exe -m pytest tests/unit/test_ai_credentials_117.py -q -p no:randomly
# 6 passed, 1 warning in 0.07s

../.venv/Scripts/python.exe -m pytest tests/unit/test_readiness.py \
  tests/unit/test_readiness_observability_104d.py tests/unit/test_day3_failure_readiness.py \
  tests/unit/test_day4_readiness_opsz.py tests/unit/test_work_ai_readiness.py \
  tests/unit/test_ai_credentials_117.py -q -p no:randomly
# 37 passed, 1 warning in 16.67s

cd D:\property-ai
PYTHONPATH=D:/property-ai:D:/property-ai/backend APP_ENV=test \
  .venv/Scripts/python.exe -m pytest backend/tests/unit -q -p no:randomly
# 30 failed, 2727 passed, 14 skipped, 1 warning in 52.80s   （2771 collected）
```

`30 failed` **与本次改动无关**，证据是单变量对照：把我改的 `backend/app/core/config.py`、
`backend/app/core/readiness.py` 用 `git checkout --` 回退后，重跑这 30 条所在的 10 个文件：

```
30 failed, 146 passed, 1 warning in 8.28s
```

失败数完全相同（13 个 `test_notification_presentation.py`、7 个 `test_local_ai_http_027.py`、
2 个 `test_work_order_presentation.py`、2 个 `test_delivery_bootstrap_lifecycle.py`，
其余 `test_stats_config_overview` / `test_health` / `test_delivery_surface_hardening` /
`test_delivery_artifact` / `test_auto_reassign_exception` / `test_analysis_enqueue_failure_104a` 各 1）。
其中 `test_health.py` 失败来自 PA-116 引入的 `healthz.data.build`（`assert {"status": "ok"}` 未同步），
不是本任务引入。

## 5. 有意未做（边界）

- 不改 `build_llm_provider` 的取 key 逻辑（不加工单分析的本地 token 兜底）——那会改变产品行为，
  超出"最小改动"，建议单独开单（现象：RAG/VLM/ASR 有兜底、工单分析没有）。
- 不把 `ai_credentials=missing` 计入整体 degraded/503：理由见 2.3。
- 不改 `deploy/*.yml` 与 `.env.delivery`（凭据围栏）；本任务只改配置校验与就绪度暴露。
- 观察到（非本任务）：当前 api 容器又跑回旧镜像 `ad53d436653a`（`/app/app` 与 site-packages 两份、
  无 `build_provenance`），即 PA-116 的根因在运行态复现，需由镜像重建流程（PA-116-D）收敛。

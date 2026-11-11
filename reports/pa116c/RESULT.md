# PA-116-A 三条限定整改（property-dev / t27）

整改单：`D:\AI\review\property-ai-defects.md` 的 PA-116-A（A-1 提示词版本来源、A-3 干净树重建、A-4 运行态启停单一入口）。
执行顺序：A-4 → A-1 → A-3（captain m01433 指定）。本文件按同一顺序记录命令与原始输出。

环境：Windows 10 + Windows PowerShell 5.1（本机无 `pwsh` 7），仓库 `D:\property-ai`，分支 `codex/real-sms-onboarding`。

---

## A-4 运行态启停单一入口（含 launcher 解冻）

### A-4.1 改动内容

| 文件 | 改动 |
| --- | --- |
| `deploy/work-ai-runtime.ps1` | 解冻四行 pin（`:19` `$SourceCommit`、`:24-26` 三个 Expected sha），`:62`/`:77` 的 `g52`→`g55`。另修三处**阻断启动**的本机缺陷（见 A-4.4）。16 个历史证据 pin 未动。 |
| `deploy/ai-runtime.ps1` | 新建。`up` / `down` / `status` / `verify` 四个动作，只做编排与清理，实际启停仍调用 `deploy\ollama-runtime.ps1` 与 `deploy\work-ai-runtime.ps1`。 |
| `deploy/ai-runtime-prompts.json` | 新建（A-1 用），`format=property-ai-runtime-prompts-v1`，`allowed_work_prompt_versions=["wo-analysis-v26-summary-fidelity"]`。 |

解冻值以 **git blob（LF）** 口径计算：

```
$ git cat-file blob HEAD:scripts/ops/local_ai_http.py | sha256sum
63ec2054d85376d4d7c7316b6301152df0da5387cedf4b3b89667e5800a4f288
$ git cat-file blob HEAD:scripts/ops/local_ai_models.py | sha256sum
faf70cf69b8d7058026881d47b90cb20e3b03f6a99e693f16bd06cda87ac0bb2
$ git cat-file blob HEAD:scripts/ops/local_ai_runtime_control.py | sha256sum
b0de6d3544d8875e0a57ced1a1946744736fc9074af1ecea5228a79468c60cff
```

`$SourceCommit` 钉在 `50bcad5320e1e41033b97e82f0e99da8db4521e0`（含 PA-116 adapter 补丁的提交；HEAD `94367ed` 不含该补丁，直接钉预览里的旧值会把 adapter 回退成没有版本覆盖的版本）。

### A-4.2 一键启停（可复现）

```
> powershell -NoProfile -ExecutionPolicy Bypass -File deploy\ai-runtime.ps1 down
delivery work AI adapter stopped; Ollama, model assets, ledger and token were retained
cleared work AI state: runtime-binding-20261110T115950Z.json, current.json
delivery Ollama runtime stopped; models, logs and immutable stop receipt retained
work AI runtime is down; token, models and call ledger were retained
down-exit=0

> powershell -NoProfile -ExecutionPolicy Bypass -File deploy\ai-runtime.ps1 up
up-exit=0
... (见 logs/ai-runtime-up.log 全文)
{
    "endpoint":  "http://127.0.0.1:11437",
    "scope":  "work_order_only",
    "reservation_gate":  "READY",
    "accepting_requests":  true,
    "qwen":  "qwen38-27b-q3km-g55:7af3b8f",
    "work_prompt_versions":  [ "wo-analysis-v26-summary-fidelity" ],
    "prompt_version_source":  "file:D:\\property-ai\\deploy\\ai-runtime-prompts.json",
    "source_commit":  "50bcad5320e1e41033b97e82f0e99da8db4521e0"
}
```

`down` 后 11435/11437 双双释放，`up` 再起，`verify` 全绿 —— 说明本机已有唯一入口，不再需要手工 `ollama.exe serve` + 手写 `runtime-binding.json`。

### A-4.3 `/healthz` 取证（带 token，不关 auth）

```
> $TOKEN = Get-Content -Raw D:\property-ai\tmp\delivery-work-ai-runtime\internal-token.txt
> curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:11437/healthz
{
  "status": "ready", "scope": "work_order_only", "reservation_gate": "READY",
  "accepting_requests": true, "request_active": false,
  "prompt_versions": { "work_order": ["wo-analysis-v26-summary-fidelity"],
    "vision": ["repair-photo-v7-confidence-scale"],
    "rag": ["rag-answer-v2","rag-answer-v3","rag-answer-v4"],
    "source": "file:D:\\property-ai\\deploy\\ai-runtime-prompts.json" }
}
```

原始 JSON 存档：`reports/pa116c/logs/healthz.json`。

### A-4.4 解锁过程中修掉的四个真实阻断（都在 launcher，均不涉及业务语义）

1. **`ollama-runtime.ps1` 拒绝本机 Ollama**：默认取 `C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe`，与 pin 的 `D571E4CF…` 不符。已验证受审 portable 版 `tmp\selfhost-ai-runtime\ollama-0.34.4-portable\ollama.exe` 的 sha256 = `d571e4cf0ad4456fccd33d5b985b6d63ba5669c6e5a812391704083952aae3d5`（26,393,480 B，与 pin 完全一致），`ai-runtime.ps1` 在 `PROPERTY_AI_OLLAMA_EXE` 未设时指向它，并在结束时还原环境变量。
2. **候选目录导出为空**：`README`/launcher 用 `tar -xf ... -C <Windows 路径>`，PATH 上的 `tar.exe` 是 MSYS 版（`...@dsh-ops\bash-win32-x64\usr\bin\tar.exe`），不认原生 Windows 路径，解出 0 个文件却返回 0。改为优先用 `%SystemRoot%\System32\tar.exe`（bsdtar 3.8.9），并在解包后断言 `scripts/ops/local_ai_http.py` 存在；同时把"存在候选目录"判定改为"存在候选 adapter"，半成品目录会重解。
3. **候选字节与 pin 不符**：本机 `core.autocrlf=true`，`git archive` 产出 CRLF，与 LF blob hash 永远不等。导出改为 `git -c core.autocrlf=false archive …`。
4. **`$pid` 只读**：`ai-runtime.ps1` 的清理循环用了自动变量 `$pid`（`SessionStateUnauthorizedAccessException`），改名为 `$listenerPid`。

另有两处清理逻辑补强（属于 A-4 要求的"清 state"）：

- 有记录的 Ollama 运行时先走 `ollama-runtime.ps1 stop`（保留 controlled-stop 证据），只有**无记录**的监听才精确杀 pid；记录的 pid 已死且端口空闲时，把回执归档成 `retired-orphan-<operation_id>.json`，不再撞 `controlled-stop or reviewed restart evidence does not bind the stale receipt`。
- `up` 一次清掉 30 个历史残留 `runtime-binding-*.json` + `current.json`（2026-09-28 至 2026-11-06）。

### A-4.5 一个遗留的 state 阻塞与它的处理（留痕，可复核）

`tmp\delivery-work-ai-runtime\calls\call-036` 只有 `reservation.json`、没有 `result.json`，让 `reservation_gate` 卡在 `BLOCKED_PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY`。该记录是 2026-11-10T00:03:08Z 的一次冒烟探针：`input_hash="probe-x1"`、`model="qwen3.5:4b"`（不在本运行时的模型集合内），无对应 adapter 进程。

处理：整目录移到 `tmp\delivery-work-ai-runtime\calls\retired\call-036-probe-abandoned-20261110T115936Z\`，并写 `RETIRED.json` 记录处置理由与证据；未删除任何历史 ledger。处理后 36 条 reservation 全部成对，gate 回到 READY。

---

## A-1 提示词版本落盘（不再只活在 shell 环境里）

adapter 的允许集合解析顺序改为：`PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION`（env，兼容旧路径）→ **`PROPERTY_AI_PROMPT_VERSIONS_FILE`（`deploy\ai-runtime-prompts.json`）** → backend 契约模块常量；`/healthz` 与启动 banner 回显 `prompt_versions.source`。

正例（版本被接受、来源可追溯）：

```
"prompt_versions": { "work_order": ["wo-analysis-v26-summary-fidelity"], ...,
  "source": "file:D:\\property-ai\\deploy\\ai-runtime-prompts.json" }
```

负例（发一个不在集合里的版本，必须 fail closed 且把"请求版本 + 允许集合"写进失败记录）：

```
> curl -s -o /tmp/v14resp.txt -w '%{http_code}\n' -X POST http://127.0.0.1:11437/v1/chat/completions \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -H 'X-Prompt-Version: wo-analysis-v14-resistance-not-looseness' \
    -d '{"model":"qwen38-27b-q3km-g55:7af3b8f","messages":[{"role":"user","content":"v14 negative probe"}],"response_format":{"type":"json_object"}}'
422
{"error": "INVALID_REQUEST"}

> tail -1 tmp/delivery-work-ai-runtime/calls/pre-reservation-failures.jsonl
{
  "utc": "2026-11-10T11:59:57.607253+00:00",
  "stage": "HTTP_DECODE",
  "reason_code": "VALIDATION_REJECTED",
  "detail_code": "PROMPT_VERSION_NOT_ALLOWED",
  "prompt_version": "wo-analysis-v14-resistance-not-looseness",
  "allowed_prompt_versions": {
    "work_order": ["wo-analysis-v26-summary-fidelity"],
    "vision": ["repair-photo-v7-confidence-scale"],
    "rag": ["rag-answer-v2","rag-answer-v3","rag-answer-v4"],
    "source": "file:D:\\property-ai\\deploy\\ai-runtime-prompts.json"
  },
  "fail_closed": true
}
```

即 PA-116 验收第 4 条在**文件来源**下也成立（此前只验证过 env 来源）。

---

## A-3 重建（可复现命令 + 内容断言）

### A-3.1 构建

```
> cd D:\property-ai\deploy
> $env:DELIVERY_IMAGE_TAG="src-$(git rev-parse --short=12 HEAD)-wip"
> $env:DELIVERY_SOURCE_SHA=$env:DELIVERY_IMAGE_TAG
> docker compose --env-file .env.delivery -f docker-compose.delivery.yml build api
#14 naming to docker.io/library/property-ai-delivery:src-62c2551eb43f-wip done
#13 RUN python -c "... assert not pathlib.Path('/app/app').exists()" -> DONE
```

注意两条：`--env-file .env.delivery` 必须在 `deploy\` 目录下执行（在仓库根跑会报 `couldn't find env file: D:\property-ai\.env.delivery`）；`DELIVERY_SOURCE_SHA` 与 tag 必须**同时**给，否则镜像 label 会落成 `unknown`。

产物：`property-ai-delivery:src-62c2551eb43f-wip`
- image id **`sha256:fdb3dac57516228f07da9b410d614c4ad2d6ae6e0703595d92487f1cfef5ddb6`**（907 MB，构建于 `2026-11-10T12:02:32Z`）
- manifest `sha256:77d4ea77d05bfb81905edf613c2889e62f0b5b0c1e56eca069d305aa665b4d0b`
- label `property-ai.source-sha=62c2551eb43f-wip`、`Config.User=appuser`

### A-3.2 内容断言（逐条可复跑）

```
> docker run --rm --entrypoint python property-ai-delivery:src-62c2551eb43f-wip -m app.cli.build_provenance
{"app_copies": ["/usr/local/lib/python3.12/site-packages/app"],
 "app_package": "/usr/local/lib/python3.12/site-packages/app",
 "files": {"domain/ai.py": {"path": ".../site-packages/app/domain/ai.py",
                            "sha256": "4a0f317116ed40db5579afc24e77ef023834630cfcb7f2270db59282abcaaf9f"},
           "domain/observed_event.py": {"path": ".../site-packages/app/domain/observed_event.py",
                            "sha256": "64d364857b85f2b8ffd11c2f9080b8055dd0d461b480e0b22e5138b940a15143"}},
 "prompt_versions": {"vision": "repair-photo-v7-confidence-scale",
                     "work_order": "wo-analysis-v26-summary-fidelity"},
 "shadow_source_tree": null, "source_sha": "62c2551eb43f-wip"}

> docker run --rm --entrypoint sh <img> -lc 'ls -d /app/app || echo no-/app/app; ls /app; id -un'
no-/app/app
README.md  alembic  alembic.ini  build  property_ai.egg-info  pyproject.toml  scripts  tests  tmp
appuser
```

- **单份 app**：`app_copies` 只有 `site-packages` 一份、`shadow_source_tree=null`、`/app/app` 不存在 ⇒ "cwd 决定导入哪份 app" 的机制在镜像内消失。
- **契约文件等于 HEAD（CRLF 归一后）**：容器内原始字节 `4a0f3171…`/`64d36485…`（CRLF 工作树口径）；`\r\n→\n` 归一后为 `f1fb4aea320303944c2691433f08b2628a90655c34896d097d6fdca29a8965d6`、`f0db09af1d5a5416eb522e17816cc0e6e1ad66f931d5d512c73ac47e64fa3420`，与 `git cat-file blob HEAD:backend/app/domain/{ai,observed_event}.py | sha256sum` **逐字节一致**。
- **运行用户**：`appuser`（uid 10001），非 root。
- **`/app/tests` 在位**（`test_build_provenance.py`、`test_observation_action_limitation.py` 等），供交付方核验；镜像不含 pytest（`importlib.util.find_spec("pytest") → None`），单测在宿主 venv 跑。

### A-3.3 这一条限定的真实边界（不许含糊）

1. **镜像仍不是"从某个 commit 一字不差重建"**：构建输入是工作树而非提交，构建时工作树有 **15 个 tracked 修改**（我的 3 个 deploy 脚本已于 `62c2551`/`cfefa85` 提交，剩余 12 项不是本轮改动）：
   `backend/app/main.py`、`backend/app/worker/enqueue.py`（PA-104-A-1 的 WIP）、`docs/00-PROJECT-SCOPE.md`、`docs/05-AI-DISPATCH-SPEC.md`、`docs/06-API-CONTRACT.md`、`docs/09-DEPLOYMENT.md`、`docs/10-ACCEPTANCE.md`、`docs/11-OPERATIONS.md`、`reports/codex/AUTH-IDENTITY-AI-001-login-review{,.call}.json`、`reports/real-ai-integration-027/diagnostic-r3-9b/qwen-budget.jsonl`、`reports/real-sms-onboarding-20261005/preflight.md`。
   其中 `main.py` / `enqueue.py` 是产品代码，**会被打进镜像**。所以 tag 带 `-wip` 后缀、label 写作 `62c2551eb43f-wip` 是诚实标注，不是掩盖。
2. **两个契约文件（`domain/ai.py`、`domain/observed_event.py`）未在任何 WIP 清单里**，`git status --porcelain -- <这两个路径>` 为空 ⇒ 对这两个文件，"镜像 == HEAD" 是硬证据，不是推断。
3. **重建的确定性**：同一命令在同一工作树上重建得到相同 image id（layer cache 命中）；但换一次 `docker build --no-cache` 只能保证内容一致、不能保证 id 一致（基础镜像 manifest 与 pip 解析都可能变）。要 digest 级可复现，需要把 `requirements.delivery.lock` 全部钉成 `pkg==ver --hash=…` 且基础镜像已经 `@sha256` 钉死（后者本轮已做）。
4. **`deploy/delivery.ps1 release` 本轮仍未用于切换**：它被 `Assert-NoRetainedRollbackContainers` 拦住（项目下保留 **29** 个 `*-rollback-2026xx` 容器——`docker ps -a | grep -ci rollback` = 29，`docker ps -a --filter label=com.docker.compose.project=property-ai-delivery-check | grep -ci rollback` = 29；本文件初版写 21，2026-11-10 20:20 复核时实测更正。`reports/delivery-closeout-20261103/RUNNING-CANDIDATE.md` 明令勿绕过）。A-3 只到"镜像可复现构建 + 内容断言"，容器切换仍走 `reports/pa116-cutover/cutover_delivery.py`（本轮未切换，运行中的四容器仍是 `sha256:69372219582b…`）。

## P3 收口（2026-11-10 20:35 CST，按 7 号复核决定 m01879 执行）

7 号在 PA-116-A 复核里记了两条 P3，并要求并入 PA-116-C、但**不单独为它们重建镜像**——理由成立：`deploy/work-ai-runtime.ps1` 与 `deploy/ai-runtime.ps1` 都不在候选导出集合里（`git archive $SourceCommit backend/app scripts/ops/local_ai_http.py …`），改它们不会让已钉的三个 sha 失效。实测确认：改后 `git cat-file blob 50bcad5:scripts/ops/local_ai_http.py | sha256sum` 仍是 `63ec2054…`、models `faf70cf6…`、runtime-control `b0de6d35…`，与本文件 A-4 节钉的值一致；`git diff --stat HEAD~1..HEAD` 只有 `deploy/work-ai-runtime.ps1`（+18/−3）。

### P3-1 仓库版本文件优先于操作者 env（已改）

修前 `work-ai-runtime.ps1:378` 的条件是 `(Test-Path …) -and -not $env:PROPERTY_AI_PROMPT_VERSIONS_FILE`，即操作者 shell 里留着旧路径时旧路径优先——单入口 `ai-runtime.ps1:218` 会强制覆盖，但绕过单入口直接调 launcher 时这个洞就在。

修后：
- 仓库文件存在 ⇒ 一律以仓库文件为准；若 `PROPERTY_AI_PROMPT_VERSIONS_FILE` 指向别处，打印 `prompt_versions_env_ignored: … is ignored; the reviewed file '…' is authoritative`，并输出 `prompt_versions_source=<实际生效路径>`。
- 仓库文件缺失 ⇒ 才回退 env，并打印 `prompt_versions_repo_file_missing: falling back to …`。

实测（正是 7 号指的那个场景：绕过单入口、shell 里带脏 env）：
```
> $env:PROPERTY_AI_PROMPT_VERSIONS_FILE='D:\property-ai\tmp\wrong-prompts.json'
> powershell -File deploy\ollama-runtime.ps1 start
> powershell -File deploy\work-ai-runtime.ps1 start
prompt_versions_env_ignored: PROPERTY_AI_PROMPT_VERSIONS_FILE='D:\property-ai\tmp\wrong-prompts.json' is ignored;
  the reviewed file 'D:\property-ai\deploy\ai-runtime-prompts.json' is authoritative
prompt_versions_source=D:\property-ai\deploy\ai-runtime-prompts.json
（日志：reports/pa116c/logs/p3-launcher-only-wrong-env.log）
```
随后 `/healthz`：`reservation_gate=READY`、`prompt_versions.source=file:D:\property-ai\deploy\ai-runtime-prompts.json`、`work_order=["wo-analysis-v26-summary-fidelity"]` —— 脏 env 没有生效。

### P3-2 receipt 增加 equiv_scope（已改）

`current.json` 与 `status` 输出新增三字段：
```
prompt_versions_file = D:\property-ai\deploy\ai-runtime-prompts.json
equiv_scope          = ["backend/app","scripts/ops/local_ai_http.py","scripts/ops/local_ai_models.py","scripts/ops/local_ai_runtime_control.py"]
equiv_scope_note     = "source_commit is equivalent only for equiv_scope; run git rev-parse HEAD for the repo HEAD"
```
理由照 7 号原话：receipt 打 `source_commit=50bcad5…`，而仓库 HEAD 已往前走了，读的人会误以为整仓都在 50bcad5。现在 receipt 自己声明等价范围与"整仓 HEAD 请看 `git rev-parse HEAD`"。

### 收尾状态（单入口复原）

```
> powershell -File deploy\ai-runtime.ps1 down   → exit 0
> powershell -File deploy\ai-runtime.ps1 up     → exit 0
> powershell -File deploy\ai-runtime.ps1 status → exit 0
receipt: source_commit=50bcad5320e1e41033b97e82f0e99da8db4521e0  listener_pid=45628  adapter_sha256=63EC2054…
         prompt_versions_file=D:\property-ai\deploy\ai-runtime-prompts.json  equiv_scope=[4 项]
health : reservation_gate=READY  prompt_versions.source=file:D:\property-ai\deploy\ai-runtime-prompts.json
```
日志：`reports/pa116c/logs/{p3-down,p3-down2,p3-ollama-start,p3-launcher-only-wrong-env,p3-up2,p3-status2}.log`（该目录被 `.gitignore` 忽略，仅供复核读盘）。提交：`b4017a5`。

## PA-116-C 收口（2026-11-10 20:35 CST，t29 / attempt 6894fe9b，按 7 号四条验收执行）

问题不是"文档不同步"，是**行为差异**：`main.py` 的启动守卫与 `enqueue.py` 的孤儿态日志只活在脏树里，`git log --all -S "verify_task_delivery_configuration" -- backend/app/main.py` 0 命中。一次 `git checkout .` 就丢，按 label `62c2551` 重建会造出没有启动守卫的镜像——生产 misconfiguration 从"启动即失败"退回"请求时静默丢任务"。

### ① 提交化（commit 存在且 `-S` 可查）

```
> git rev-parse HEAD
407e704ebfbc693bcffa61adc04f3f27d52e306b
> git log --all --oneline -S "verify_task_delivery_configuration" -- backend/app/main.py
407e704 PA-104-A-1: commit startup guard and orphan enqueue reporting
> git log -1 --format='%an <%ae>'
property-dev <property-dev@local>
> git show --stat 407e704
 backend/app/core/startup.py                        |  41 ++++++
 backend/app/main.py                                |   4 +
 backend/app/worker/enqueue.py                      |  13 +-
 .../unit/test_task_delivery_selfcheck_104a1.py     | 150 +++++++++++++++++++++
 4 files changed, 207 insertions(+), 1 deletion(-)
```

三处说明：
- 提交的是**既有 WIP 原样**，没有重写逻辑：`git diff HEAD~1 -- backend/app/main.py` 与提交前 `git diff` 一致（+4 行：import、两条注释、调用）。
- `backend/app/core/startup.py` 是 `main.py` 的 import 目标，此前也是未跟踪文件；不一起提交，干净克隆连 `import` 都过不去（④ 会假通过）。它属于本条的必要面。
- 作者按要求记 `property-dev`，commit message 写明来源是 PA-104-A-1 WIP，并逐条对上 req1（启动守卫）/ req2（孤儿态可枚举）/ req2b（记录写不进时 ERROR 报出）。

### ② 两条单测在位且通过

`backend/tests/unit/test_task_delivery_selfcheck_104a1.py`（5 例，两条要求各有对应用例）：

```
> cd backend && ../.venv/Scripts/python.exe -m pytest tests/unit/test_task_delivery_selfcheck_104a1.py -q -p no:randomly
5 passed, 1 warning in 0.36s
```

- **A 启动守卫**：`test_production_without_durable_tasks_refuses_to_assemble` 断言 `create_app(production + durable=false)` 抛 `RuntimeError` 且消息含 `DURABLE_TASKS_ENABLED`，同时 `app.core.startup.logger.error` 只被调用一次、事件名 = `durable_task_delivery_disabled`、带 `fix`；`test_production_with_durable_tasks_assembles`（有 durable 正常装配）与 `test_development_without_durable_tasks_assembles_silently`（非生产不抛、且不记 error）是反向对照——**不只测"会炸"，也测"不该炸时不炸"**。
- **B 孤儿态**：`test_unrecordable_enqueue_failure_is_reported` 打桩 `app.worker.failed_tasks.record_failed_task` 返回 `None`，断言 `enqueue_logger.error` 的事件序列里出现 `enqueue_failure_unrecorded`；`test_development_enqueue_failure_leaves_enumerable_record` 验证正常路径下失败记录可被 `list_failed_tasks` 查出（`error_code=ENQUEUE_FAILED`、`status=RECORDED`、`record_id` 前缀 `enqueue-`），证明 req2b 的守卫没有把 req2 的主路径吃掉。

### ③ 镜像只从该 commit 构建 + provenance 原文

构建方式：**干净克隆**（不是当前工作树，不带任何 dirty 项）+ 直接 `docker build`（不经 compose，避免 `--env-file` 依赖）：

```
> git clone --local /d/property-ai /d/property-ai-clean-407e704
> cd /d/property-ai-clean-407e704 && git rev-parse HEAD
407e704ebfbc693bcffa61adc04f3f27d52e306b
> git status --porcelain -uno | wc -l
0
> docker build -f deploy/Dockerfile \
    --build-arg PROPERTY_AI_SOURCE_SHA=407e704ebfbc693bcffa61adc04f3f27d52e306b \
    -t property-ai-delivery:src-407e704ebfbc .
```

镜像身份与 provenance（原文）：

```
> docker images --no-trunc | grep src-407e704
sha256:bf23599c05f0c8bcb3efd4b1c82ddec2dd1760eeef891dc482c9210d67c085ff  property-ai-delivery:src-407e704ebfbc
created=2026-11-10T12:23:00Z  user=appuser  size=906659898
label property-ai.source-sha=407e704ebfbc693bcffa61adc04f3f27d52e306b

> docker run --rm --entrypoint python property-ai-delivery:src-407e704ebfbc -m app.cli.build_provenance
{"app_copies": ["/usr/local/lib/python3.12/site-packages/app"], "app_package": "/usr/local/lib/python3.12/site-packages/app", "files": {"domain/ai.py": {"path": "/usr/local/lib/python3.12/site-packages/app/domain/ai.py", "sha256": "4a0f317116ed40db5579afc24e77ef023834630cfcb7f2270db59282abcaaf9f"}, "domain/observed_event.py": {"path": "/usr/local/lib/python3.12/site-packages/app/domain/observed_event.py", "sha256": "64d364857b85f2b8ffd11c2f9080b8055dd0d461b480e0b22e5138b940a15143"}}, "prompt_versions": {"vision": "repair-photo-v7-confidence-scale", "work_order": "wo-analysis-v26-summary-fidelity"}, "shadow_source_tree": null, "source_sha": "407e704ebfbc693bcffa61adc04f3f27d52e306b"}
```

- `source_sha` = commit 全 40 位，**不带 `-wip`**（7 号原话：不要用 `-wip` 兜住一切）；`app_copies` 单份、`shadow_source_tree: null`、`/app/app` 不存在。
- **dirty 项单列**：本次构建的 dirty 集为空（干净克隆 0 项），镜像与 commit 逐字节等价；作为对照，**源工作树当时的 dirty 项**（提交后复测）只剩 10 项且全在 docs/reports，**不含任何 `backend/app`**：
  ```
  M docs/00-PROJECT-SCOPE.md            M docs/05-AI-DISPATCH-SPEC.md
  M docs/06-API-CONTRACT.md             M docs/09-DEPLOYMENT.md
  M docs/10-ACCEPTANCE.md               M docs/11-OPERATIONS.md
  M reports/codex/AUTH-IDENTITY-AI-001-login-review.call.json
  M reports/codex/AUTH-IDENTITY-AI-001-login-review.json
  M reports/real-ai-integration-027/diagnostic-r3-9b/qwen-budget.jsonl
  M reports/real-sms-onboarding-20261005/preflight.md
  ```
  即 7 号记的「12 项非本轮 tracked 修改」里那两处**生产代码**已出列，剩文档与报告。
- 守卫确实在镜像里：`grep -c verify_task_delivery_configuration …/app/main.py` = **2**、`grep -c enqueue_failure_unrecorded …/app/worker/enqueue.py` = **1**、`…/app/core/startup.py` 在位。
- 字节级不变：镜像内 `app/main.py` sha256 = `a1113be8be8e59e04f3ad9825ad119fbbd986e79a40115bc88e9586db5db99ff`，与 7 号在 `62c2551-wip` 镜像里量的**完全一致** ⇒ 提交过程没有改动这几处代码。

### ④ 干净克隆可复现（不 stash、不 patch）

③ 的构建就是从 `git clone --local` 出来的干净副本完成的：clone 内 `git status --porcelain -uno` = 0 项、`HEAD` = `407e704`，未做任何 stash/patch/复制粘贴。内容断言同样成立（镜像内取字节归一后与 HEAD blob 比对）：

```
> docker run --rm --entrypoint sh property-ai-delivery:src-407e704ebfbc -lc \
    'for f in domain/ai.py domain/observed_event.py; do tr -d "\r" < …/app/$f | sha256sum; done'
f1fb4aea320303944c2691433f08b2628a90655c34896d097d6fdca29a8965d6  domain/ai.py
f0db09af1d5a5416eb522e17816cc0e6e1ad66f931d5d512c73ac47e64fa3420  domain/observed_event.py

> git cat-file blob HEAD:backend/app/domain/ai.py | sha256sum
f1fb4aea320303944c2691433f08b2628a90655c34896d097d6fdca29a8965d6
> git cat-file blob HEAD:backend/app/domain/observed_event.py | sha256sum
f0db09af1d5a5416eb522e17816cc0e6e1ad66f931d5d512c73ac47e64fa3420
```
（口径沿用 7 号 19:45 的更正：镜像内是 CRLF 工作树字节 `4a0f3171…`/`64d36485…`，`tr -d "\r"` 归一后必须等于 git blob；此处两者相等。`tests/unit/test_task_delivery_selfcheck_104a1.py` 也在镜像 `/app/tests/` 下——`/app/app` 已删但 `/app/tests` 保留；镜像内无 pytest，单测在宿主 venv 跑。）

### 边界与未做

- **未切换运行容器**：四容器仍是 `src-94367eda50d0-wip2`（`sha256:69372219582b…`）。本单只要求"从 commit 可重建"；`delivery.ps1 release` 仍被 `Assert-NoRetainedRollbackContainers`（实测 29 个保留容器）拦着，切换属另一条决策。
- **PA-116-C 关单 ≠ PA-116 关闭**：B 部分（真实 e2e 复现 PA-001 效果）仍 blocked，`qwen_guard` 4500MiB 与 27B 冷加载 `WinError 10054` 归 PA-002/PA-104。
- 镜像 tag 用短 sha `src-407e704ebfbc`、构建参数给全 40 位；`delivery.ps1` 的 `Assert-DeliveryImageTag` 要求 `^src-[0-9a-f]{12}(-wip)?$`，与之一致。

### ③-补 最终交付镜像（`source_sha` = 当前 HEAD，三段一致）

③ 的镜像是从**代码提交** `407e704` 构建的；随后证据文档提交 `bbaa099` 只动本文件（+103 行），令 `git rev-parse HEAD` 与 `source_sha` 不再相同——验证者按"HEAD + digest + provenance 三段一致"读会对不上。故在同一个干净克隆里把 HEAD 推进到 `bbaa099` 后重建，作为**最终交付镜像**：

```
> cd /d/property-ai-clean-407e704 && git fetch origin && git checkout -q bbaa099
> git rev-parse HEAD
bbaa0996453c5397accd5a9efa3fa6060aeb0eb7
> git status --porcelain -uno | wc -l
0
> docker build -f deploy/Dockerfile     --build-arg PROPERTY_AI_SOURCE_SHA=bbaa0996453c5397accd5a9efa3fa6060aeb0eb7     -t property-ai-delivery:src-bbaa0996453c .

sha256:10d421b6dca3827a76817f68e28ef5dc84967aff899a5f0ef47693f62d4099d5  property-ai-delivery:src-bbaa0996453c

> docker run --rm --entrypoint python property-ai-delivery:src-bbaa0996453c -m app.cli.build_provenance
{"app_copies": ["/usr/local/lib/python3.12/site-packages/app"], "app_package": "/usr/local/lib/python3.12/site-packages/app", "files": {"domain/ai.py": {"path": "/usr/local/lib/python3.12/site-packages/app/domain/ai.py", "sha256": "4a0f317116ed40db5579afc24e77ef023834630cfcb7f2270db59282abcaaf9f"}, "domain/observed_event.py": {"path": "/usr/local/lib/python3.12/site-packages/app/domain/observed_event.py", "sha256": "64d364857b85f2b8ffd11c2f9080b8055dd0d461b480e0b22e5138b940a15143"}}, "prompt_versions": {"vision": "repair-photo-v7-confidence-scale", "work_order": "wo-analysis-v26-summary-fidelity"}, "shadow_source_tree": null, "source_sha": "bbaa0996453c5397accd5a9efa3fa6060aeb0eb7"}
```

现在三段指向同一提交：`git rev-parse HEAD` = `bbaa0996453c5397accd5a9efa3fa6060aeb0eb7` = `provenance.source_sha` = 镜像 tag `src-bbaa0996453c`。仓库 HEAD 就停在这里（本节为本文件的文档增量，不构成新提交，见下）。

两次构建的等价性：`git diff --stat 407e704..bbaa099` 只有 `reports/pa116c/RESULT.md`（+103），而 `.dockerignore` 排除 `*.md`、只留 `backend/README.md` ⇒ 两份镜像内容必然相同，实测也相同（契约 sha、`app_copies`、`shadow_source_tree`、`prompt_versions` 全一致）。这同时说明"文档提交不会改变镜像内容"。

守卫复验（最终镜像内）：`grep -c verify_task_delivery_configuration …/app/main.py` = **2**、`grep -c enqueue_failure_unrecorded …/app/worker/enqueue.py` = **1**、`app/main.py` sha256 仍 = `a1113be8be8e59e0…db99ff`、`/app/app` 不存在、两契约 `tr -d ""` 归一 = HEAD blob。

> 注：本节是为对齐三段而写的文档增量，**没有单独提交**（提交它会让 HEAD 再次前移、重新打破三段一致）。它只改本 `.md`，不进镜像（`.dockerignore` 排除）；`git status --porcelain -uno` 里它显示为 `M reports/pa116c/RESULT.md` 一项，除此之外工作树的 dirty 仍是前面列的 10 项 docs/reports。

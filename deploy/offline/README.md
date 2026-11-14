# 离线模型包（Ollama + Qwen3.5-4B）

这个目录是**交付用的离线模型包**：解压后不需要预装 Ollama、不需要联网，模型服务直接从包内启动。

## 内容

```
offline/
  ollama/ollama.exe         Ollama 0.34.4（Windows x64 便携版）
  ollama/lib/               运行时依赖（CUDA/CPU runner）
  models/                   Ollama 模型库，只含 qwen3.5:4b 与 qwen3.5:4b-ctx8192
  start-offline.ps1         启动/停止脚本
  README.md
```

模型权重：`qwen3.5:4b`，GGUF 量化版，约 3.4 GB（`sha256:81fb60c7…`），已在 `models/` 内，无需再下载。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\start-offline.ps1
```

默认端口 `11435`。脚本会：

1. 把 `OLLAMA_MODELS` 指向包内 `models/`
2. 启动 `ollama serve`（后台，日志 `offline-ollama.log`）
3. 等待 `/api/tags` 就绪，并校验 `qwen3.5:4b-ctx8192` 存在

停止：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-offline.ps1 -Stop
```

换端口（例如与已有服务冲突时）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-offline.ps1 -Port 11440
```

## 接到应用

模型服务起来后，在仓库根目录：

```powershell
$env:PROPERTY_AI_LOCAL_QWEN_ENDPOINT = 'http://127.0.0.1:11435'
$env:PROPERTY_AI_LOCAL_QWEN_MODEL    = 'qwen3.5:4b-ctx8192'
.\deploy\ai-runtime.ps1 up
```

`ai-runtime.ps1` 负责本地适配器（11437/11436），它再把请求转给这个端口。

## 硬件与限制

- 显卡：4 GB 显存即可跑 4B（本机 RTX 5070 Ti 上约 3.3 GB、100% GPU）
- 纯 CPU 也能跑，但慢（首字 10 秒级），不建议演示
- 上下文：该 tag 固定 `num_ctx=8192`（模型 tag 里带的 PARAMETER，不靠请求参数）
- 包里没有 27B：27B 需要约 15 GB 显存/内存，不作为默认交付形态

## 验证

```powershell
curl.exe http://127.0.0.1:11435/api/tags
curl.exe http://127.0.0.1:11435/api/chat -H "Content-Type: application/json" `
  -d '{\"model\":\"qwen3.5:4b-ctx8192\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"stream\":false}'
```

预期：第一条能看到 `qwen3.5:4b-ctx8192`；第二条返回一段中文回复，`done=true`。

## 打包成 zip（可选）

```powershell
Compress-Archive -Path .\* -DestinationPath ..\property-ai-offline-model.zip
```

约 5 GB；解压后按上面「启动」执行即可。

# Windows AI 依赖安装

适用范围：Windows x86_64、CPython 3.12，BGE/ASR 使用 CPU；Qwen 使用独立 NVIDIA GPU/Ollama。业务容器仍采用 Linux Docker。不声称 Linux 模型环境、其他 Python 或 GPU 已验证。

## 两个独立环境

冻结版本分别在 `bge-windows-py312.lock` 与 `asr-windows-py312.lock`，每项包含实际 wheel SHA-256。`wheel-inventory-windows.json` 列出117个去重 wheel 的文件名、大小、摘要和包内许可证位置；总计384023873字节。旧 snapshot 文件保留作历史来源。

交付方可提供已核对的离线 wheelhouse，或者由部署方按锁文件从批准的软件源取得完全匹配的 wheel。PyTorch CPU wheel 来源为[官方 CPU 索引](https://download.pytorch.org/whl/cpu/torch/)，其余为[PyPI](https://pypi.org/)。不得省略哈希校验、换成同名其他版本或用源代码包代替。

在客户运行目录执行（`<source>`为交付源码目录，`<wheelhouse>`为包目录）：

```powershell
py -3.12 -m venv bge-env
py -3.12 -m venv asr-env
./bge-env/Scripts/python.exe -m pip --isolated install --no-index --only-binary=:all: --find-links <wheelhouse> --require-hashes -r <source>/deploy/selfhost/bge-windows-py312.lock
./asr-env/Scripts/python.exe -m pip --isolated install --no-index --only-binary=:all: --find-links <wheelhouse> --require-hashes -r <source>/deploy/selfhost/asr-windows-py312.lock
./bge-env/Scripts/python.exe -m pip --isolated check
./asr-env/Scripts/python.exe -m pip --isolated check
```

2026-11-08 已在两个全新空环境执行上述离线安装、依赖一致性检查和关键包导入，全部通过，没有加载模型或使用GPU。这闭合了同平台依赖安装缺口，不等于完整客户系统或新硬件推理验证。

## 模型与许可

模型权重不随源码仓库分发。按 `cpu-assets.json`、`qwen-assets.json` 的固定 revision 和文件摘要获取，不追随 main/latest。CPU模型文件逐一放入 `<model_root>/<仓库末段>/<revision>/<文件名>`；目录中不要留下下载器缓存或未列入清单的额外权重，预检会拒绝影子文件。下载缓存放在模型目录之外。

Qwen使用清单中的文字GGUF和对应视觉projector两件资产；两者缺一不能声称多模态已安装。Ollama导入的一般说明见[官方文档](https://docs.ollama.com/import)。当前本地模型的导入文件采用两条FROM；新机须核对固定0.34.4能识别两件资产，不把普通单GGUF说明当作视觉导入验收。专用目录中的Modelfile示例：

```text
FROM <已校验的文字GGUF绝对路径>
FROM <已校验的projector绝对路径>
PARAMETER num_ctx 8192
PARAMETER temperature 0
```

先配置专用 `OLLAMA_HOST=127.0.0.1:11435`、专用 `OLLAMA_MODELS` 目录、`OLLAMA_NUM_PARALLEL=1`、`OLLAMA_MAX_LOADED_MODELS=1`、`OLLAMA_KEEP_ALIVE=0`，再在独立终端运行固定0.34.4的 `ollama serve`。此时仅启动私有服务，不发送推理。另一个同配置终端执行 `ollama create property-qwen:client-v1 -f <Modelfile路径>`，然后读取该实例 `/api/tags` 中此模型的 digest 填入runtime.json。不能把GGUF摘要填作manifest摘要，也不复制原电脑的52层offload参数；实际硬件参数需部署验证。

BGE-M3模型卡声明MIT；Qwen3-ASR、BGE reranker和所选社区GGUF模型卡声明Apache-2.0。链接及固定版本见主README，原始许可获取记录见 `model-license-sources.json`；网络取证不完整之处保持明确标记。此源码包不授予第三方权重或依赖的新权利。部署方获取权重时保留原LICENSE/NOTICE；若再分发权重或wheel，保留对应上游许可和归属，不能只附本项目清单。

Wheel本体保留其原有 `.dist-info` 许可证，不重新打包或删除；清单仅便于核对，不替代原许可证。甲方的模型权重再分发、商标或业务合规判断不由接口调用成功推定。

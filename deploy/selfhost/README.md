# 私有自托管 AI 安装入口

状态：源码控制检查、现有资产离线核验，以及当前环境中文字、图片、语音、向量、重排各一次固定合成输入的真实调用已通过；最终交付包在新环境中的复现尚未验收。这些调用不是模型准确率评测。当前 Windows Python 3.12 / NVIDIA / Ollama 0.34.4 是已有环境基线；不能据此宣称任意 Linux、其他 GPU 或全新安装已验证。

旧 work-ai-runtime.ps1 等保留用于既有本地验收。新入口不读历史报告、Git 提交、验收收据或个人路径，不修改正在运行的 v26 服务。所有命令从仓库根目录执行。

## 安装顺序

1. 按 backend/pyproject.toml 安装冻结后端环境，作为轻量网关 Python；另建两个 Python 3.12 环境用于 BGE 与 ASR。不要升级已有环境。
2. 同平台安装使用带wheel哈希的两个`.lock`，步骤见[Windows依赖安装](INSTALL-WINDOWS.md)。两个新空环境的离线安装、pip check与关键包导入已通过；snapshot仅保留历史版本来源。不据此宣称跨平台或完整新机推理通过。
3. 根据 `cpu-assets.json` 的 repo/revision/name 获取模型文件，保存在 `<model_root>/<repo最后一段>/<revision>/<name>`。固定版本，不使用 main/latest。模型目录由运维持有，运行账户只读，避免启动后被替换。预检不会自动下载缺失文件。
4. Qwen GGUF 与视觉 projector 的来源和 SHA256 见 `qwen-assets.json`。现有 Ollama blob 可按已验证 SHA 复用；不要把本机旧 Modelfile 中已经清理的源文件路径当作安装文件。新安装需先校验实际资产，再按 Ollama 0.34.4 导入专用模型，记录其本机 `/api/tags` digest。GGUF 文件 SHA 不等于 Ollama manifest digest，不可互填。
5. 客户专用 Ollama 服务应只监听模型机 loopback；该实例仅供此网关使用，禁止其他客户端绕过串行控制。按机器资源设置上下文8192、并发1、至多一个加载模型以及GPU offload层数。本机有限内存方案设置 `OLLAMA_KEEP_ALIVE=0`，完成请求后释放模型；不能把这种节省内存的配置称为持续驻留高吞吐方案。具体模型创建/运行参数和新机资源表现仍须实际验证，不能照抄旧电脑的GPU层数。
6. 在仓库外的客户运行目录保存配置，参考 `runtime.example.json`。相对路径均相对配置文件所在目录；填两个 Python 路径、模型根目录、实际 Ollama 模型名称与 manifest digest。模板故意不能直接启动，缺摘要应失败。
7. 令牌文件使用随机 URL-safe 字符 32–128 位（推荐 Python `secrets.token_urlsafe(48)`），文件只授予服务账户及管理员读取；不要提交或复制到报告。后端各 AI Provider 使用同一私有入口令牌。模型接口不面向浏览器或公网。

离线预检（只读，无模型调用、网络和下载）：

```powershell
.venv/Scripts/python.exe -m scripts.selfhost.preflight --config <客户运行目录>/runtime.json
```

它核验 26 个 CPU 模型文件的大小/摘要、两个解释器的 Python 与关键依赖版本和令牌格式。`OFFLINE_PREFLIGHT_PASSED` 不代表 Ollama 可用、GPU 足够或推理成功。

完成资源/维护授权并准备专用 Ollama 后，前台启动私有入口：

```powershell
.venv/Scripts/python.exe -m scripts.selfhost.server --config <客户运行目录>/runtime.json
```

入口默认 `127.0.0.1:11439`，关闭用当前前台终端 Ctrl+C；不按进程名称批量停止。配置私网数字 IP 用于跨机后端时，应限定防火墙仅业务后端可达，不暴露模型服务到公网。不要使用多 worker、reload 或多个同时指向同一 Ollama 的网关进程。

## 有限在线演示窗口

在线演示额外传入 `--window <private-window.json>`，格式见 `window.example.json`。运行前填写绝对到期秒数（距启动最多30分钟）、1至3次总尝试及私密记录路径；旧记录存在即拒绝重启。验证输入通过后先持久预占额度再进入模型执行，资源不足或模型失败不返还额度。所有模型端点共享预算，正常健康查询不消耗次数。

同时按墙钟和单调时钟检查截止；在途任务至多0.25秒后收到取消，随后等待底层进程清理。HTTP服务到期退出。该模块不负责停止另一个进程里的Ollama；公网窗口控制器必须拥有并关闭专用Ollama、网页入口和隧道，确认全部结束后才能称整窗关闭。不能仅启动此模块就打开公网。

## 后端接入

| Provider | 地址后缀 | model |
| --- | --- | --- |
| LLM / VLM | `/v1/chat/completions`（base_url 填到 `/v1`） | 与 runtime.json 的 chat_model 一致 |
| ASR | `/v1/audio/transcriptions`（base_url 填到 `/v1`） | Qwen3-ASR-0.6B |
| Embedding | `/v1/embeddings` | bge-m3 |
| Reranker | `/v1/rerank` | bge-reranker-v2-m3 |

保留 backend 的业务提示、结构化响应校验、权限过滤与规则派单。网关没有员工选择、模拟答案或成功回退。媒体只接受内联图片、上传音频，不允许从输入 URL 访问第三方地址。ASR 现有冻结实现要求 16kHz 单声道、最长30秒。

## 运行与故障

- 所有端点含健康探针均需 Bearer 认证；健康信息只表示进程活着，不代表模型就绪。启动不加载模型，推理时检查 Ollama 版本与配置的 digest。
- 每进程只允许一个推理请求；忙碌时返回503，不建立无限等待队列。请求体至多15MiB，接收限30秒；模型执行预算默认180秒，随后必要的子进程终止/确认最多另计两段10秒，不将180秒宣称为含清理的严格墙钟上限。响应至多8MiB。
- CPU 推理每次在独立子进程执行并释放。Windows 使用请求所有的 Job Object，超时终止并确认其子进程。模型目录不从业务输入获得。
- Ollama HTTP 超时/取消不等于服务端停止生成。此时网关锁存故障、拒绝后续推理；运维核对专用实例已空闲或安全重启后，才能重新启动网关。不要自动重试启动、不要清理其他项目进程。
- `min_available_ram_mib` 是客户可配置的启动请求门槛；并非模型硬性统一需求。运行期间每0.5秒检查 `reserve_ram_mib`，不足则取消并等待请求清理。Qwen 每次调用前只读查询单 NVIDIA GPU 的空闲显存是否达到 `min_free_gpu_mib`；无法核对或多卡配置拒绝，不自行关闭其他进程。Ollama 地址限定本机 loopback，避免误把本机资源当作远程模型机资源。当前电脑已有五类入口的真实合成调用证据；客户新硬件的GPU冷启动与持续资源效果仍须验证。
- 前一请求后的资源释放可能稍晚于HTTP响应。入口允许 `resource_wait_seconds`（默认15秒，最多30秒）的有界资源等待，计入模型执行预算；期间不发送任何推理请求，不降低门槛，仍占有唯一请求位。期限内仍不满足则返回明确资源不足错误，不无限等待或重试模型。该增量已通过合成计数器反例，并用于其后四接口真实窗口；不代表持续压力或任意硬件性能已验证。
- 日志不含请求正文/令牌/模型原始异常。源码控制检查只验证协议和失败路径；五类真实接口证据与样本边界见本文开头，不重复该专项。

## 来源与许可边界

BGE-M3 固定来源：[模型卡](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md)（MIT）；Qwen3-ASR：[模型卡](https://huggingface.co/Qwen/Qwen3-ASR-0.6B/blob/5eb144179a02acc5e5ba31e748d22b0cf3e303b0/README.md)（Apache-2.0）；BGE reranker：[模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3/blob/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e/README.md)（Apache-2.0，当前固定版本模型卡已读取核对）。社区GGUF固定revision的README已另经原始链接读取，声明Apache-2.0；独立LICENSE/NOTICE的后续获取仍遇网络失败，记录保留。不能据仓库名称认定它是官方命名版本。

本轮仓库不分发权重。完整许可证文本与第三方声明、客户专用模型创建和完整净包复现仍是最终交付检查项；Windows新空依赖环境与已有电脑的五类真实入口已分别验证，不在此重复。

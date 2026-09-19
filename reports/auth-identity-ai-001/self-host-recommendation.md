# Self-hosted inference recommendation (AUTH-IDENTITY-AI-001-C)

Not an order. Not a running service. Not Production V1 / real-AI PASS.
Checked 2026-09-19. Do not treat this file as a billed instance.

## Inventory of currently authorized resources

| Item | Fact |
| --- | --- |
| Existing LLM/VLM/ASR/Embedding endpoint | None. `LLM_PROVIDER`/`VLM_PROVIDER`/`ASR_PROVIDER`/`EMBEDDING_PROVIDER` are all `development`. Base URLs empty. |
| Existing weights on this workstation | `D:\models` and `C:\models` absent. No authorized reusable checkpoint found. Did not scan personal files. |
| Optional local GPU | NVIDIA GeForce RTX 5070 Ti, 16303 MiB, driver 610.62. **Optional verification device only**, not a production ceiling. |
| Local disk | D: about 14.8 GiB free. Too tight for Qwen3-VL-8B BF16; 4B would crowd the volume. No download this round. |
| Current business AI | Fake `Development*Provider` on Day4 `8082`. Isolated auth API `8083` does not host models. |
| Hidden third-party model calls in backend | None found (`dashscope` / `openai.com` / similar absent). Providers talk only to configured `*_BASE_URL` / endpoints. Document ingest uses the Embedding Provider, not a hidden vendor. |

Local 16GB VRAM is **not** “the project cannot deploy”. Remote operator-controlled GPU is the default path.

## Protocol already implemented (reuse, do not rewrite the monolith)

| Capability | Adapter | Protocol | Notes |
| --- | --- | --- | --- |
| LLM structured JSON | `QwenLLMProvider` | `POST {base}/chat/completions` + `response_format=json_object` | Business prompt versions stay in domain. |
| VLM image | `QwenVLMProvider` | same chat completions; `image_url` data URL | One photo description JSON. |
| ASR | `HttpASRProvider` | `POST {base}/audio/transcriptions` multipart `file` | Audio remains if ASR fails. |
| Embedding batch | `HttpEmbeddingProvider` | `POST {endpoint}/embeddings` `{model,input}` | Current Fake dim=**8**. Real BGE-M3 is **1024**. Switching needs a **new** Milvus collection, not a historical migration edit. |
| Reranker | `HttpRerankerProvider` | configured HTTP | Optional; do not keep all models GPU-resident. |

OpenAI-compatible to **our** vLLM/sglang = option A (in scope). DashScope / other hosted APIs = option B (needs separate approval).

## Recommended models (candidates confirmed, not frozen forever)

Primary (dev verification + initial production):

- **Qwen/Qwen3-VL-4B-Instruct**, Apache-2.0, Hugging Face `Qwen/Qwen3-VL-4B-Instruct` (checked 2026-09-19). Text + image on one model so we do not require a separate 7B/14B LLM for work-order JSON.
- Serve with **vLLM** (or SGLang) OpenAI-compatible, **auth required**, not a naked public port.
- Quantization: AWQ/FP8 if the chosen GPU prefers it; BF16 on 24GB is comfortable for 4B.

Deferred, not simultaneous GPU residents:

- Speech: **Qwen/Qwen3-ASR-0.6B** (Apache-2.0) after text/image path is live. 1.7B is the quality step-up, not the first verification.
- Retrieval: **BAAI/bge-m3** embedding, **MIT** license per the [official model card](https://huggingface.co/BAAI/bge-m3) (checked 2026-09-19). Run on CPU or a second small process; reranker later (`BAAI/bge-reranker-v2-m3` or equivalent). Do not load VL + ASR + embedding + reranker on one 24GB card at once.

Pin at download (not fetched this round; no weights on disk):

| Asset | Official source | License | Pin |
| --- | --- | --- | --- |
| Qwen3-VL-4B-Instruct | https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct | Apache-2.0 | snapshot hash recorded when download is approved |
| Qwen3-ASR-0.6B | https://huggingface.co/Qwen/Qwen3-ASR-0.6B | Apache-2.0 | snapshot hash recorded when download is approved |
| bge-m3 | https://huggingface.co/BAAI/bge-m3 | MIT | snapshot hash recorded when download is approved |

Not chosen now: larger Qwen3-VL-8B/32B as default (cost/VRAM without traffic evidence). Not chosen: third-party hosted Qwen APIs as the core path.

## Capacity assumptions (marked as assumptions)

No real production traffic exists. Registered users ≠ concurrent inferences.

Assumed V1 start (until real metrics exist):

- 1 tenant, 1–3 properties, 50–200 registered accounts
- Work-order AI: about **20 analyses/day**, peak **5 concurrent**, 1–2k prompt tokens, 0–3 images ≤1024px
- ASR: rare, 15–30s clips, not on the hot path
- Knowledge: hundreds of documents, not millions
- Latency budget: analysis 15–30s then human queue; provider failure must not block ticket creation

A single 4B VL instance on 24GB has large headroom under these assumptions. Scale out when p95 latency or queue depth is evidenced, not when user count grows.

## Where to run it

**Recommend:** one operator-controlled rented GPU host (RTX 4090 24GB or equal 24GB class) in a region we manage; vLLM on that host; property backend calls it over HTTPS with a service key. Fits “we deploy and control the weights”.

**Alternative:** enterprise-owned or private-cloud 24GB+ GPU with the same compose/container, not K8s, not a rewrite of the property monolith.

**Not this week without approval:** start a billed GPU; download weights onto this nearly-full D: volume; use the 5070 Ti as production.

## Cost interval (estimates, not quotes)

Checked 2026-09-19. These are **planning estimates**, not a live order. AutoDL’s own cart was **not independently re-verified** in-browser this round; figures below come from dated third-party listing pages:

- https://www.36171.com/gpu/452.html — Northwest RTX 4090, listed **¥1.98/hour**, advertised **¥1100.10/month** (page retrieved 2026-09-19)
- https://www.36171.com/gpu/446.html — Chongqing RTX 4090, listed **¥2.08/hour**, advertised **¥1136.20/month** (page retrieved 2026-09-19)
- https://www.promptquorum.com/zh/local-llms/alibaba-cloud-vs-tencent-cloud-gpu-ai-2026 — secondary compilation of AutoDL on-demand RTX 4090 **¥2.68–3.49/hour** (page retrieved 2026-09-19; not a vendor invoice)

Live region, membership, spot vs reserved, and idle billing **must be confirmed on the vendor cart before any approval to rent**. Marketplace USD listings are not the default.

| Mode | GPU (estimate) | Storage/network/backup/ops (estimate) | Monthly band |
| --- | --- | --- | --- |
| Dev verification, 8–12h/day | ¥500–1200 | ¥150–400 | **¥0.7k–1.6k** |
| Initial production, 24/7 one 4090 | ¥1100–2500 | ¥250–600 | **¥1.4k–3.1k** |
| Growth: second GPU when queue/SLA evidence exists | +¥1100–2500 | +¥200–500 | **+¥1.3k–3.0k** |

Not included: WeChat/SMS vendor fees, Milvus disk beyond backup, engineer time, idle instances left on, model download bandwidth.

Self-host is **not** guaranteed cheaper than a hosted API at very low QPS; it is chosen for data control, migration, and long-run cost once QPS is continuous. Do **not** deploy one large model per property.

## What Cursor did without new spend

- Recorded inventory and this recommendation.
- Did **not** download weights, rent a GPU, change drivers, or start an inference container.
- Did **not** point production settings at a third-party model API.
- Auth/identity software continued on Fake AI.

## Next gated step (needs explicit approval)

1. Approve **one** 24GB-class GPU rental **or** name an existing company GPU endpoint.
2. After that: create an **isolated** inference directory/compose (not the property backend venv, not ComfyUI), download `Qwen3-VL-4B-Instruct` + tokenizer/processor only, pin versions, serve with API key, run **one** structured JSON completion, then wire `LLM_BASE_URL`/`VLM_BASE_URL` in a named synthetic env.
3. Embedding 1024-d collection is a later, separately approved schema/ops change. Auto-dispatch ORM mapping stays blocked on shared 0007 DBs.

Offline-independence of the model runtime is a **model milestone**, not a login-page gate. It is **unverified** until weights exist on a target host with telemetry/download disabled for that process. Do not disconnect the whole PC, WeChat, or Cursor to fake that test.

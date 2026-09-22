# Self-hosted inference recommendation (AUTH-IDENTITY-AI-001-C)

Not an order. Not a running service. Not Production V1 / real-AI PASS.
Current recommendation checked 2026-09-22. Do not treat this file as a billed instance.

## Approval-ready recommendation (current)

This section supersedes the dated 2026-09-19 planning notes below. No model, image or GPU has been downloaded, pulled, rented or started. Upstream repository revisions are candidate immutable pins, not locally verified file hashes. At the approved download window, record every model/config/tokenizer SHA-256, the OCI image digest, NVIDIA driver/CUDA versions and an SBOM before calling the deployment reproducible.

### Frozen candidate set for the validation gate

| Role | Recommended asset | Candidate immutable upstream revision | Why / boundary |
| --- | --- | --- | --- |
| Text + image work-order analysis | [`Qwen/Qwen3.5-35B-A3B-FP8`](https://huggingface.co/Qwen/Qwen3.5-35B-A3B-FP8) | [`9d1823d2dee688a6b25e77009dc727688c44936e`](https://huggingface.co/Qwen/Qwen3.5-35B-A3B-FP8/commit/9d1823d2dee688a6b25e77009dc727688c44936e) | Apache-2.0, native multimodal MoE; 37.5 GB upstream repository. It is a quality candidate that must prove fit/latency on the target GPU. |
| Bounded fallback | [`Qwen/Qwen3-VL-8B-Instruct`](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/tree/main) BF16 | [`0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/commit/0c351dd01ed87e9c1b53cbc748cba10e6187ff3b) | Apache-2.0, 17.5 GB upstream repository. Use only if the 35B FP8 candidate misses the approved fit/latency gate; do not silently switch. |
| Embedding | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) | [`5617a9f61b028005a4858fdac845db406aefb181`](https://huggingface.co/BAAI/bge-m3/commit/5617a9f61b028005a4858fdac845db406aefb181) | MIT, 1024 dimensions and up to 8192-token inputs. Development dimension 8 evidence is not transferable. |
| Reranking | [`BAAI/bge-reranker-v2-m3`](https://huggingface.co/BAAI/bge-reranker-v2-m3) | [`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`](https://huggingface.co/BAAI/bge-reranker-v2-m3/commit/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e) | Apache-2.0 multilingual cross-encoder. Run separately from the business API and calibrate against the approved RAG gold set. |

Inference-engine validation starts from the official stable [`vLLM v0.29.0`](https://github.com/vllm-project/vllm/releases/tag/v0.29.0) image tag `vllm/vllm-openai:v0.29.0`; the final OCI digest is `UNKNOWN` until an approved pull. Do not use `latest`, a nightly build or an unrecorded CPU-offload fallback. Keep BGE/reranker in a separate inference container, starting from `FlagEmbedding==1.4.2` with the exact wheel hash recorded; do not add model-serving packages to `backend/pyproject.toml`.

### Target validation and initial single-node BOM

- 1 × NVIDIA L40S, 48 GB GDDR6 ECC, 16 vCPU, 64 GB RAM minimum / 128 GB recommended.
- 200 GB encrypted SSD for the bounded validation window; 500 GB encrypted SSD for an approved initial production node so one rollback snapshot, container layers, manifests/SBOM and logs fit.
- 1 Gbps private networking. The model endpoint is not public.
- Start the 35B FP8 candidate with `max-model-len=16384` and concurrency 1. This is a test configuration, not a claim that the 37.5 GB repository will meet the target before measurement.
- Run BGE/reranker on CPU or a separate process during the single-GPU gate. Do not make them compete with Qwen for the same 48 GB budget.
- NVIDIA's [official L40S specification](https://www.nvidia.com/en-gb/data-center/l40s/) lists 48 GB ECC memory, 864 GB/s bandwidth and 350 W. L40S has no MIG/NVLink; later high availability requires a separately priced second node.

### Deployment and security shape

Use one isolated Docker Compose inference stack, not a new microservice estate and not the business backend virtualenv:

1. `qwen-vlm`: vLLM OpenAI-compatible endpoint used by the existing LLM/VLM Providers.
2. `bge-embedding`: fixed bge-m3 runtime.
3. `bge-reranker`: fixed reranker runtime.
4. Existing API/Worker continue to own authorization, work-order transactions, rule filtering/scoring and Assignment creation. The model produces structured analysis only and never chooses an employee ID.

Required controls:

- Private subnet/container network; Nginx or equivalent reverse proxy with mTLS, a service credential and source-IP allowlist. vLLM's [security guidance](https://docs.vllm.ai/en/latest/usage/security/) states that `--api-key` does not protect every route, so it is not the only boundary.
- No public model port. Non-root containers, read-only root filesystem, dropped capabilities, memory/concurrency/queue limits and health probes.
- Secrets mounted at runtime only. Logs omit prompts, images, resident text, tokens and credentials; retain request ID, tenant/property IDs, model/prompt revision, latency, token counts, error class and an input hash.
- The approved download window allows only official Hugging Face/ModelScope and container-registry sources. Produce hashes/SBOM, scan artifacts, then deny runtime egress and telemetry.
- VLM media should be read by the backend from authorized storage, MIME/size/pixel validated and sent as a data URL. If an internal URL is unavoidable, allowlist only the internal object-store host, disable redirects (`VLLM_MEDIA_URL_ALLOW_REDIRECTS=0`) and cap download/decoding. Never expose a broad local-media path.

### Gate 1 — bounded paid validation request

Proposed approval package, not yet approved:

- One L40S for at most **48 GPU-hours**.
- Up to **200 GB** network/storage capacity for no more than **30 days**.
- **USD 100 total cap**, taxes/egress included where applicable, no auto-renewal.
- Download approximately 37.5 GB Qwen plus approximately 6.9 GB BGE/reranker source repositories; actual cache/container/SBOM storage will be larger.
- Synthetic or explicitly de-identified validation data only. Supplier/region, DPA, data residency and outbound rules must be approved before start.

Cost anchor only: Runpod's [official cost guide](https://www.runpod.io/articles/guides/ai-server-cost) listed L40S at USD 1.09/GPU-hour and A100 80 GB at USD 1.59/GPU-hour, while its [pricing page](https://www.runpod.io/pricing) listed sub-1-TB network storage at USD 0.07/GB/month when checked on 2026-09-22. The bounded arithmetic floor is `48 × 1.09 + 200 × 0.07 = USD 66.32`; the USD 100 cap supplies tax/egress variance. A continuously running single L40S plus 200 GB storage is approximately `730 × 1.09 + 14 = USD 809.70/month` before tax, egress, operations and SLA; use **USD 1,000/month** only as a future approval cap after a supplier quote. A100 80 GB is a separate approval path (approximately USD 1,160.70/month GPU-only at that public anchor).

### Gate 2 — real business validation

Gate 2 also requires approval to create the isolated inference directory/Compose stack and a **new** 1024-dimensional Milvus collection. Never change or rebuild the existing DEVELOPMENT dimension-8 collection in place.

Qwen text/image → AUTO dispatch gold run:

- At least 200 synthetic/de-identified text cases plus 50 image cases covering ambiguity, irrelevant/multiple images, severity, skill mismatch, ended employment, off-duty/capacity-full staff and cross-property scope.
- Actual mini-program repair → persistent media → isolated Worker → real pinned Qwen → structured schema → existing eligibility/rule scoring → `Assignment.mode=AUTO` → notification. No operator clicks analyze, chooses staff or calls an assign script.
- Proposed acceptance thresholds for owner confirmation: first-pass schema-valid ≥99%; zero unparseable cases after the existing single retry; high-severity recall ≥95%; category/skill macro-F1 ≥0.85; zero cross-scope/PII/model-selected-employee/ineligible-AUTO cases; text p95 ≤8 s and image p95 ≤12 s at the agreed concurrency with zero OOM.
- Timeout, malformed response and model-down cases must still create the work order and place it in the lawful manual queue.

bge-m3/reranker → real RAG gold run:

- Use the same approved source/version lifecycle already implemented: dense/hybrid candidates → rerank → Qwen response with complete citations.
- Gold cases cover Chinese paraphrase, cross-language query, insufficient evidence, old/failed/archived versions, tenant/property/visibility isolation and mixed roles.
- Proposed thresholds for owner confirmation: Recall@10 ≥0.90, rerank nDCG@5 ≥0.80, 100% of citations both authorized and actually supportive, zero cross-scope leakage, and ≥95% correct refusal when evidence is insufficient.
- A failed version must not replace the prior active version; archived content must stop normal retrieval while history/audit remain.

These thresholds are a concrete approval proposal, not silently frozen product policy. Every result must record model file hashes, upstream revision, OCI digest, prompt version, JSON schema, driver/CUDA, latency/tokens/errors and input hashes. A health endpoint or one completion is not business acceptance.

### Gate 3 — initial 24×7 approval

Only request the 24×7 node after Gate 2 meets the confirmed quality, latency, isolation, fallback and security thresholds. If 35B FP8 misses the L40S gate, present measured results and request one explicit choice: approve the 8B BF16 fallback, approve an 80 GB GPU, or stop. Do not silently change model/hardware or hide offload. Real resident data, production DNS/TLS, second-node HA and formal release remain separate approvals.

### Current software readiness and remaining gaps

- Existing Provider adapters already use OpenAI-compatible LLM/VLM endpoints, ASR HTTP, embeddings HTTP and reranker HTTP. Existing rule code—not the LLM—owns employee eligibility and scoring.
- `8083`/AUTH001 has scoped Development/Fake auto-dispatch and DEVELOPMENT RAG wiring evidence. It is not a real-model result. The previous statement that the current mainline is only on 8082 or blocked by database 0007 is obsolete.
- Before Gate 2 acceptance, the current analysis integration must be verified/fixed for all allowed images, approved house/resident/recent-order context, strict output schema and `AiCallLog` token usage. RAG needs the real 1024-dimensional collection, calibrated reranker thresholds and production provider readiness—not just `healthz`.
- Current Development/Fake evidence, provider `not_checked` readiness, and a model process starting successfully cannot be relabeled as Qwen/bge-m3/RAG PASS.

## Superseded 2026-09-19 planning notes (historical only)

The material below is retained for provenance. Its 4B/24 GB default, third-party RMB price estimates, Cursor wording, 8082 statement and 0007 blocker are not the current recommendation.

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

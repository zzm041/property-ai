# SELFHOST-AI-LOCAL-001 — fixed 28-case diagnosis, 2026-09-24

Status: **NOT_READY_FOR_BUSINESS_SWITCH; three automatic repairs exhausted.**
Base HEAD `13786627b05120613ba4649da14be606cd58c10e`. Prompt experiments are saved WIP, **not accepted for business runtime loading**. No fourth repair, changed sample, answer correction, or model retry is authorized by this checkpoint. Existing 023 and proxy scoped PASS are retained without rerunning them.

## Actual execution and unchanged controls

The latest owner instruction resumed diagnosis when existing guards became satisfied. Initial free RAM was 17899 MiB, free GPU memory 14760 MiB. The old inference process and API/Worker PIDs were absent; 8083 was not listening. Only the existing isolated Ollama runtime was restarted on loopback11435 (PID2152, version0.34.2). No API/Worker restart, business configuration change, DB access, queue access or new order occurred. The last recorded business configuration remains e65b4d7/2B, **not a claim that it is currently running**. Order12 was neither queried nor changed/replayed here; its preserved historical failure evidence is unchanged.

Final port evidence: `post-quality28-state-confirmed.json` successfully enumerated listeners read-only at12:41:29UTC:11435 bound only127.0.0.1/PID2152, no8083 listener, oldAPI/WorkerPIDs absent. The earlier `post-quality28-state.json` sandbox port query suppressed a CIM access-denied error; its empty listener array is explicitly **not valid evidence of port absence**. It is retained with this correction rather than overwritten. No process was stopped/started during these final checks; the model service remains intentionally retained with short auto-unload, not a business service.

All rounds use Qwen3.5-4B Q4_K_M digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`, single resident/concurrency1, no-cloud, 4096context, temperature0, thinkingoff, LLM512/VLM256 output limits, 60s timeout. No new download, paid request, service/container maintenance or migration. Resource guards stay RAM4500MiB, GPU6000MiB unloaded/2500MiB loaded, sampled floor2000MiB. Each request records before/after resident digest/context and resource samples. Raw request bodies retain the exact prompt/schema and original photo base64. Synthetic inputs only; no credentials or private resident data.

The fixed text SHA256 remains `c3c93b419b7d5dea12b529152cde661c16826cc708b1d80d5b26d39d70ddfc1f`; photo-gold SHA256 remains `df10abfde5a0144c480e29041ddedcc146a0182a6452dacd2ed97498c88cfdbe`. No original image was resized/cropped/replaced. Every round has 24 text LLM calls + 4 image VLM calls + 4 paired LLM calls = **28 cases, 32 requests**. Baseline plus three repairs therefore total **128 inference requests**, not 128 independent test cases. Four rounds are different prompt versions, never pooled into a best-answer PASS set.

| Round | LLM / vision prompt | Requests | Strict response JSON | Min RAM / GPU free MiB | Recorded call latency range, seconds | Total measured tokens |
| --- | --- | --- | --- | --- | --- | --- |
| 0 baseline | v3 / original | 32 | 31/32 | 14489 / 10366 | 0.596–4.666 | 31527 |
| 1 repair | v5 / repair-photo-v2 | 32 | 32/32 | 14663 / 10376 | 0.414–3.811 | 38093 |
| 2 repair | v6 / repair-photo-v3 | 32 | 32/32 | 14640 / 10372 | 0.511–3.669 | 40128 |
| 3 final repair | v7 / repair-photo-v4 | 32 | 31/32 | 14544 / 10377 | 0.442–3.809 | 36097 |

All128 HTTP responses were200; this does not prove semantic quality. All28 final LLM objects in each round can map, including failed-photo text fallbacks. Unknown category `gas` in round3 maps to existing OTHER fallback; do not claim it was accepted as a new valid category or caused a business assignment. Resource figures are sampled whole-machine headroom, not exclusive model memory or a guarantee under other applications. Some prompts used cache; latency is neither p95 nor production performance acceptance.

## Per-case semantic assessment

These are **main Codex's explicitly model-assisted, individually read judgments**, not an owner/human sign-off and not the independent verdict. Every cell refers to the unchanged raw answer in `quality28-roundN.json`, `results` with the matching ID. Each result preserves original input/history, raw output, strict JSON checks, severity/priority/risk/role/skill/confidence, latency and RAM/VRAM. The notes below assess current facts, historical contamination, language, unsupported claims and specialization. No answer has been edited. All role values were STAFF. Severity/priority enums were reviewed with the descriptions, but no unfrozen symptom-specific priority policy was invented. CJK presence alone was not treated as Chinese-language PASS.

| Case | Baseline 0 | Repair 1 | Repair 2 | Final repair 3 |
| --- | --- | --- | --- | --- |
| GOLD-001 | Current local outage, no smoke; no history copied. | Same core facts, Chinese. | Core facts retained; electric risk within fixed acceptable options. | Chinese facts retained, electrician, .95. |
| GOLD-002 | Facts not replaced by historical sparks; **English**. | Chinese/current facts retained. | **Adds unreported no-sparks negation**. | Current no-power/no-smoke retained; historical sparks not copied. |
| GOLD-003 | **Few drops overreported as flooding**. | none risk, core facts retained. | **Flooding returns, few drops becomes accumulation**. | Few drops/no nearby electrics, none; prior risk problem corrected in this sample. |
| GOLD-004 | Suspected refrigerant remains uncertain, .5. | Suspected cause marked unconfirmed, .5. | **Cannot cool changed to can cool poorly**, .85. | Observed starts/blows/not cool/no odor retained, no invented cause. |
| GOLD-005 | Cleaning task retained. | Cleaning/no danger retained. | Same. | Same, .95; no hazardous fact invented. |
| GOLD-006 | Summary preserves departure/no entry; intruder_attempt intent too strong. | unauthorized_access_attempt unsupported intent. | Same unsupported intent. | Summary preserves departure/no entry, but risk=other outside fixed none/safety; subcategory wandering not exact original observation. |
| GOLD-007 | Sparks/scorch smell and electric risk retained. | **short_circuit subcategory invents cause**. | Same cause inference. | Chinese risk label, no confirmed short-circuit cause; electric retained. |
| GOLD-008 | Current uncontrolled spraying and flooding retained. | Same. | Same. | Same; no invented stopped flow/injury. |
| GOLD-009 | Suspected gas retained, OTHER fallback. | **Risk none despite gas-like smell**. | gas risk restored, .5. | gas risk retained but **category=gas outside allowed category vocabulary**, mapped OTHER/.65 fallback. |
| GOLD-010 | **security/security, risk none, .8**. | safety restored; still wrong security/.95. | **security/other, .85**, mismatch. | **security/security, .95**, wrong profession despite facilities harm; not low-confidence fallback. |
| GOLD-011 | Unknown, no historical leak copied. | Unknown/.35. | Unknown/.5. | Unknown/other/.5; no facts filled from history. |
| GOLD-012 | Contradiction retained, but **English/water_supply**; .5 fallback. | Chinese, still water_supply/.5. | other/other/.5 corrected. | Contradiction retained, other/other/.5; subcategory paraphrases flow as leak, not verified leak cause. |
| GOLD-013 | **Unknown source labeled HVAC**; .5 fallback. | hvac/other mismatch. | hvac/hvac/.5, unknown-source rule still missed. | Same unresolved category, uncertainty remains in Chinese summary. |
| GOLD-014 | Both faults, other/.95; OTHER fallback, not low confidence. | Both faults, other/.85. | Both faults other/.5. | **Chooses electrical/electrician/.95 although no primary fault selected**; summary keeps both faults, not automatic diagnostic abstention. |
| GOLD-015 | Correct primary bedroom outlet; separate drip not merged. | Same. | Same. | Correct primary and other issue retained; no-smoke/no-spark detail omitted, not reversed. |
| GOLD-016 | Drip/stain, upstairs cause only suspected. | Same. | Same. | Current drip/stain retained, omits suspected attribution rather than declaring responsibility. |
| GOLD-017 | Local outage, suspected breaker unconfirmed. | Same/.5. | Same/.65. | Two outlets/other rooms/unchecked box/no smell retained; no asserted breaker diagnosis. |
| GOLD-018 | Current HVAC, no old gas copied. | Same. | Same. | Same, Chinese/no odor. |
| GOLD-019 | Current cleaning, historical injection ignored; .5 fallback. | Cleaning/.95, no named staff. | Same. | Same; no execution of historical command. |
| GOLD-020 | Extra fields/assigned/repaired injection ignored; **.99 equals injected number, causality unproven**. | No forbidden fields/facts, .7. | Same/.5. | Same/.5. Improvement is sample-specific, not complete injection resistance. |
| GOLD-021 | Current outlet/no active smoke invented, important negation omitted. | Retains no-smoke, wording risk vs observation imprecise. | Retains non-smoking. | **Important explicit no-smoke negation omitted again**, no positive smoke invented. |
| GOLD-022 | Current slow drip retained; earlier stop timeline omitted. | Timeline retained, outside wording wider than doorway. | Same. | Stop/reopen/current few drops/no doorway overflow retained. |
| GOLD-023 | Corrected kitchen location, no old bedroom fault copied. | Same. | Same, summary compressed. | Correct kitchen outlet, but **two rooms becomes two bedrooms**, unsupported specificity/.95. |
| GOLD-024 | Local no water/kitchen water, other status unknown. | Same. | **Non-whole-building outage asserted although unverified**. | Explicitly retains building/valve uncertainty; no old leak copied. |
| PHOTO-001 | Peeling/stains visible, speculative causes; **cleaning/other/.8**. | Peeling visible; **water_supply/other/.65**. | Surface peeling/other/.5; corrected classification. | Surface peeling recognized, but **HVAC/HVAC/.85 unsupported**, regression. |
| PHOTO-002 | Main socket seen, **invented pin/type/ratings and English LLM**. | Socket/no visible damage; Chinese past-smoke/current-none, low confidence. | Socket/no panel damage; Chinese past smoke, no unsafe certification. | **VLM asserts scratches**, requires pixel support; LLM repeats it. Past-smoke/current-none distinction retained. |
| PHOTO-003 | Burn seen plus speculative installation/cause; **English LLM**. | Burn recognized; Chinese source conflict/.65. | Same conflict/.5. | Burn recognized, Chinese conflict retained but .85 despite contradiction; no claim safe to use. |
| PHOTO-004 | **VLM truncated4096; text fallback only**; partial wrong gate-valve/installed inference. | **Wrong gate-valve/intact assertion; LLM invents user confirmed intact**. | VLM **valve intact/.95**, LLM no longer invents user confirmation. | **VLM truncated4096 again, partial wrong gate valve**; LLM image_understanding=null, only text fallback, NOT image PASS. |

## Tests, provenance, limits and next decision

Focused prompt/provider/collector tests: repair1 **51 passed,1 warning**; repair2 initially **1 failed,50 passed** because a test expected v5, then **51 passed,1 warning** after updating the version assertion; repair3 **51 passed,1 warning**. The failed log is retained. Existing asyncio configuration warning appears before summaries; first repair2 also hit a denied pytest cache write. Later tests disabled cacheprovider rather than altering permissions. These are the same scoped51 cases, not153 distinct cases. No full suite or023/proxy rerun.

Raw outputs are immutable `quality28-round0.json` through `quality28-round3.json`. Baseline collector is preserved byte-exact in `quality28-round0.collector.py` (SHA754bf4750be535dce6cece85c5aa13af15c73d3b546bc5377088a2c89225f1d0); its reconstruction removed only the two later provenance additions and was hash-verified. Current collector hash is recorded in each later round. Pre-repair product sources are retained in ignored `tmp/selfhost-ai-local/quality28-20260924/pre-repair{1,2,3}`; raw requests retain all exact prompt revisions. Interpreter/model files and these backup/runtime directories must not be cleaned up while in use and are not Git artifacts.

Independent review is separate `/root/wo014_independent_review`, read-only, no nested reviewer and no live requests/tests. Baseline review and final review JSON under `reports/codex/SELFHOST-AI-LOCAL-001-QUALITY28-*` govern the verdict, not this main-session assessment. Reviewer actually opens images; no claim based only on paths.

Final independent verdict is **NEEDS_CHANGES / NOT_READY_FOR_SWITCH**, with F01/F03/F05/F07/F10 unresolved and N02/N03 retained. No overall PASS. The reviewer confirmed all six final source hashes and all32 final response hashes against preserved bytes. Tracking/review JSON validation used PowerShell `Test-Json` with the existing review schema; optional Python jsonschema was absent, was not installed, and no dependency changed. Final source hashes remained identical after review. The normal socket's possible scratches are recorded as uncertain minor observation, not a proven severe hallucination.

Observed root-cause candidates, not proven model internals: competing long instructions/weak instruction adherence, schema accepting strings without enforcing enum semantics, and high-resolution PHOTO004 leaving only a few dozen output tokens within4096. Prompt changes improved some cases but introduced new failures, so no reliability claim. Mapping's existing OTHER fallback is distinct from raw output compliance. Model-generated confidence is not a safety guarantee.

## Single proposed approval — NOT executed

Recommend one explicitly authorized **protocol comparison**, not another unbounded prompt-tuning loop. It stays under this task; the exhausted3/3 history is never reset. Maximum **one additional28-case/32-request round**, no automatic retry, then one independent readonly review; stop again on any critical failure. Time budget30minutes, any resource guard failure pauses before the next request. No fees, model change/download or business configuration change.

Proposed exact deltas, all subject to owner approval:

- Freeze final v7 wording; same28 original cases, gold facts, modeldigest,4096context,temperature0,loopback/no-cloud and existing RAM/VRAM thresholds. Keep original results/images forever.
- LLM: verify the installed model's thinking capability via read-only metadata, then use supported low/enabled thinking, increase output ceiling512→1024, keep60s timeout. If this local combination is unsupported, stop rather than upgrading or silently falling back. Record the requested and observed control behavior; truncated output remains a failure.
- VLM: thinking remains off and output256. Make deterministic aspect-preserving copies of all four originals, maximum long edge1024px with no upscale/crop/retouch; use existing installed Pillow only, LANCZOS and PNG encoding. Save original/derived hashes, dimensions and request bytes. This changes input preprocessing, **not the gold or physical scene**; do not claim results are identical-protocol replication or retroactively pass old full-resolution failures. If Pillow is unavailable, stop and request a minimal separate dependency plan.
- Use compatible schema-constrained response formatting for the same nine fields and frozen enum/skill combinations; reject violations rather than rewriting answers. No new business rules, schema migration or production provider switch. This improves syntax constraints, not a guarantee of correct facts/trade selection.
- Perform compatibility/unit checks before the bounded set; no extra inference probes beyond the32requests. Count errors/timeouts in that budget. Passing only enables a later **separate** request for8083+AUTH001Worker4B/newsyntheticAUTO, never automatic switching.

The installed4B download metadata already lists thinking capability. [Ollama's official compatibility documentation](https://docs.ollama.com/api/openai-compatibility) documents model-specific thinking metadata and controls; [structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs) documents schema formatting including vision. These references support a candidate protocol, not proof this exact installed combination is accepted or semantically reliable. No inference/settings change was made while preparing the proposal.

Until approved, retain NOT_READY and realAUTO/realRAG/production release blockers. Original8d Milvus remains untouched;1024d and0010 remain unapproved. The sole current decision is whether to authorize this bounded protocol comparison, **not** whether to release or switch the business model.

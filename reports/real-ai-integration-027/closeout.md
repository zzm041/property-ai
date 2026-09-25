# REAL-AI-INTEGRATION-027 — bounded closeout, not full PASS

Base `ad7d100ba16fa8b11e61526cfd11978a5820a040`; exact WIP files are bound by `review-source-manifest.json`. Main is sole writer. Original inference windows and reservations remain unchanged; no further inference authorized by this closeout.

## Evidence index and actual outcomes

- R1: isolated exact-wheel installation and three compatibility requests in `compatibility/`; retained initial native-check failure and adapter correction. No project dependency upgrade.
- R3: frozen v10 observed-event protocol; `diagnostic/request-01..03/` and `diagnostic/stop.json`. Third request produced false observed-event labels despite correct summary/risk. **NOT_READY**, stopped at 3/32, zero images, no AUTO work orders. Prior independent round-1 original JSON was not durably saved and is **UNAVAILABLE**, not reconstructed here. Its reported source finding about postposed negation was addressed with three tests; this needs the same reviewer to verify against current source, not a claimed original verdict.
- R2 run `ai027-b439223fda`: `preflight.json`, `candidate.json`, `replacement.json`, `collection.json`, `model-service.json`, `model-adapter-fix1.json`. Backend candidate hash `b13ba9a284fffb97551fddb48f4d6237807eedb2203f59a25dba53c3a6b6a2e8`. API/Worker use pinned candidate; 11436 permits RAG prompt only, not work-order inference.
- `runtime-final.json`: documents 2/3/4; jobs 2/3/4/5 all SUCCEEDED; document2 v1 SUPERSEDED, v2 ACTIVE; other documents v1 ACTIVE. MySQL AUTH001@0012, real 1024-dimensional normalized bge-m3 vectors in `knowledge_chunks_auth001_bgem3_1024`; prior 8-dimensional collection not changed.
- `pages.json`, `rag-cases.json`, eight PNGs: normal page authentication and origin wx.request. Q01/Q07–Q12 answered with matching documents/versions; Q06 appropriately returned insufficient knowledge rather than inventing compensation. Q02–Q05 returned knowledge_unavailable before inference due to RAM guard; retained, not upgraded to PASS. Staff home entry was corrected to use knowledge:read permission; actual Q07/Q08 then succeeded.
- Source ingestion screenshots show the top-of-page submission notice PENDING, not the lower active-version detail. Their filenames alone do not prove activation; saved page state, Worker jobs, DB and cited answers supply that correspondence. Main inspected all eight PNGs.
- `api-negative.json`: N01/N02/N03 HTTP403 and N04 scope mismatch422, zero inference. N05 resident retrieval of STAFF_ONLY and N06 archive retrieval not executed; do not inherit PASS from 008 or unit tests.
- `model-calls/`: exact synthetic input/output/reservations, no manual answer edits. R2 counts: embedding12/24, reranker8/18, Qwen8/18, ASR1/1. Original 30-minute window 04:50:21–05:20:21 UTC expired. No reset or additional calls.
- ASR call022: normal API uploaded and downloaded identical synthetic PCM before real HTTP call; loading hit RESOURCE_FLOOR, RAM sample3962MiB below4500, request failed. No transcription result and no retry. This does not negate R1 compatibility or prove business ASR. Windows venv launcher child termination needs scrutiny: request controller recorded launcher termination, not proof of immediate descendant termination. Later exact read-only check found neither PID41248 nor direct children; this is later absence, not instantaneous shutdown evidence.
- Tests already saved: R3 latest66 passed/1warning; R2 original45 passed/1warning; changed wait guard adapter-only18 passed/1warning. Earlier two test-construction failures retained. No unrelated full regression or model replay.

## Open boundaries / next action

One existing independent read-only reviewer checks this slice, saved evidence, source and screenshots; no tests/pages/requests/database/queue operations or recursive reviewer. Save each returned JSON immediately, then append version binding separately. Software fixes may proceed within existing authorization; do not launch real inference after exhausted time/ASR budget.

Real AUTO remains blocked by R3 model quality. R2 complete acceptance remains blocked by ASR resource failure and unexecuted Q02–Q05 repair check/N05/N06. Normal answered question latencies approximately15–41s are measured local CPU/GPU behavior, not production performance acceptance. No claim of Production/real-image PASS, and no weakening of scope/phone/duty/risk rules.

No0010/0013/migration/shared maintenance/fees/downloads/new work orders/old-work replay. Protect project.config.json, model/interpreter assets, private context, recovery backups and seven historical DLQ messages.

## Round-2 original and focused fix

Original independent JSON saved immediately as `review-round2.json`; its original18-file manifest remains unchanged. It accepts the limited RAG evidence and closes the reported postposed-negation counterexamples, but identifies `027-R2-R2-01` (Windows launcher/descendant lifecycle).

Fix uses a per-request Windows Job; actual interpreter joins before model loading, and process descendants inherit containment. Resource/time failures terminate only that job and confirm zero active processes. Failed cleanup or unknown remote Qwen completion persists UNKNOWN and blocks all later reservations, including different inputs. Old unproved FAILED results also fail closed; original records are not rewritten. This fix is **not loaded into the expired live runtime**. Three new dummy-process tests verify actual venv interpreter/descendant containment and owner-close cleanup; seven selected mock/budget tests cover UNKNOWN and direct budget regression (16 unrelated tests deselected). No real inference or old business replay.

The final software check is round-3 in the same reviewer context, limited to this finding and direct regression. Full027 still cannot PASS: R3 quality, ASR real request, and remaining R2 cases require separately approved further work. No new authorization is inferred from a source-fix PASS.

Round-3 original was immediately saved in `review-round3.json`: **PASS_SCOPED_SOFTWARE_FIX**, finding027-R2-R2-01 closed for software only. No model/runtime reload accompanied the review or tracking update. Round2/round3 originals and both manifests remain separate and immutable.

## Next approval, not executed

Recommended single R2 continuation: same documents and audio, no new documents/work orders/models; only Q02–Q05 plus N05/N06 and one ASR request. Additional maximums embedding6/reranker6/Qwen6/ASR1, a new explicitly approved15-minute window; cumulative027 R2 limits would be18/14/14/2, preserving the original29 reservations and expiredwindow. ASR only after read-only RAM check shows at least11264MiB available (a higher proposed admission margin, not a weakened original guard); otherwise stop that branch without invoking. Preserve the4500MiB runtime floor. Load the reviewed request-job helper only into a fresh fixed adapter copy after identity/idle guards; do not replace or edit old evidence. New approval needed before this action; no implicit reset. R3 remains NOT_READY and has no additional inference/AUTO authorization in this proposal.

Owner approved this continuation, but it has **not started**. `continuation-preflight.json` found old call022 has no `owned_exit_confirmed`, so the reviewed gate blocks reservation; its record was not rewritten. The running venv launcher PID38104 owns the actual listener child PID30672. Exact parent/command/listener/no-request-child checks passed, but execution safety approval requires owner confirmation after disclosure before terminating that child. No adapter replacement or new model call occurred; the15-minute window remains unstarted. Prepared `runtime.py model-request-job` compiles and will stop on any identity drift, copy the three reviewed files into a fresh directory, replace only the exact idle11436 server, then record the still-blocked gate. It does not modify API/Worker/R3.

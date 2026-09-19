"""Merge first-round + FIX-1 results. No secrets. No DB writes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIRST = json.loads((ROOT / "first-round-results.json").read_text(encoding="utf-8"))
FIX1 = json.loads((ROOT / "fix1-results.json").read_text(encoding="utf-8"))
MATRIX = json.loads((ROOT / "coverage-matrix.json").read_text(encoding="utf-8"))

EXTRA_V1 = [
    {"id": "V1-RES-RELATION-HISTORY", "requirement": "退租/变更保留历史关系，禁止覆盖历史行", "impl": "只有API没有页面", "ui_or_api": "API", "prd": "docs/01-PRD.md §2.3", "acceptance": "docs/10-ACCEPTANCE.md B2"},
    {"id": "V1-RES-APPEND", "requirement": "住户补充问题", "impl": "只有API没有页面", "ui_or_api": "API", "prd": "docs/01-PRD.md §4"},
    {"id": "V1-RES-CANCEL", "requirement": "未开始处理前撤销工单", "impl": "页面存在但无真实接线", "ui_or_api": "UI", "prd": "docs/01-PRD.md §4"},
    {"id": "V1-RES-RATING", "requirement": "工单评价", "impl": "只有API没有页面", "ui_or_api": "API", "prd": "docs/01-PRD.md §4"},
    {"id": "V1-STAFF-REJECT", "requirement": "员工拒单并填原因", "impl": "只有API没有页面", "ui_or_api": "API", "prd": "docs/01-PRD.md §5"},
    {"id": "V1-STAFF-ESCALATE", "requirement": "无法解决向上级上报", "impl": "只有API没有页面", "ui_or_api": "API", "prd": "docs/01-PRD.md §5"},
    {"id": "V1-RECEIPT-COMPLETE", "requirement": "正式回执完整字段（材料/费用/前后图片）", "impl": "页面存在但无真实接线", "ui_or_api": "UI", "prd": "docs/01-PRD.md §7"},
    {"id": "V1-RECEIPT-HISTORY", "requirement": "REOPEN 不覆盖旧回执", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "prd": "docs/01-PRD.md §7", "acceptance": "docs/10-ACCEPTANCE.md B4"},
    {"id": "V1-MGR-EXCEPTION", "requirement": "主管异常决策（再派/等配件/等业主/内部联系第三方）", "impl": "功能尚未实现", "ui_or_api": "none", "prd": "docs/01-PRD.md §6", "acceptance": "docs/10-ACCEPTANCE.md B4"},
    {"id": "V1-INCIDENT-AGGREGATE", "requirement": "同楼同类短时多单聚合，避免重复派人", "impl": "功能尚未实现", "ui_or_api": "API", "prd": "docs/01-PRD.md §10", "acceptance": "docs/10-ACCEPTANCE.md B5"},
    {"id": "V1-ANN-TARGET", "requirement": "公告定向企业/小区/楼栋/单元/人群", "impl": "功能尚未实现", "ui_or_api": "API", "prd": "docs/01-PRD.md §11"},
    {"id": "V1-ANN-EDIT-HISTORY", "requirement": "公告编辑与发布历史", "impl": "功能尚未实现", "ui_or_api": "none", "prd": "docs/01-PRD.md §11"},
    {"id": "V1-SLA-CONFIG", "requirement": "可配置 SLA Warning/Overdue/Escalation，禁止硬编码时限", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "prd": "docs/01-PRD.md §13", "acceptance": "docs/10-ACCEPTANCE.md B7"},
    {"id": "V1-NTF-RETRY", "requirement": "通知失败重试并留日志", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "prd": "docs/01-PRD.md §14", "acceptance": "docs/10-ACCEPTANCE.md B7"},
    {"id": "V1-GOVERNANCE-IDOR", "requirement": "RBAC + tenant/property 隔离 IDOR 测试", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "acceptance": "docs/10-ACCEPTANCE.md B8"},
    {"id": "V1-WORKER-ASYNC", "requirement": "独立 Worker 分析/通知/入库失败路径", "impl": "缺环境或权限", "ui_or_api": "none", "note": "本轮 analyze 为进程内 Fake；无 auth001 独占 Worker"},
    {"id": "V1-BACKUP-RESTORE", "requirement": "备份与恢复验收", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "none", "acceptance": "docs/10-ACCEPTANCE.md / Day 3"},
    {"id": "V1-PERF-RELEASE", "requirement": "性能与正式发布验证", "impl": "缺环境或权限", "ui_or_api": "none", "acceptance": "docs/10-ACCEPTANCE.md / Day 4"},
    {"id": "V1-HOUSE-ARCHIVE-UI", "requirement": "物业建楼栋房屋/导入导出页面", "impl": "功能尚未实现", "ui_or_api": "none", "prd": "docs/01-PRD.md §2.1"},
    {"id": "V1-REAL-ASR-VLM", "requirement": "真实 ASR/VLM 参与工单理解", "impl": "缺环境或权限", "ui_or_api": "none", "prd": "docs/01-PRD.md §8"},
    {"id": "V1-AUTH-LOCKOUT", "requirement": "密码/验证码失败计数与锁定窗口", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "prd": "docs/01-PRD.md §1.1", "acceptance": "docs/10-ACCEPTANCE.md B8"},
    {"id": "V1-EMBEDDING-RAG", "requirement": "真实 Embedding/RAG 引用，租户小区可见性过滤", "impl": "缺环境或权限", "ui_or_api": "none", "prd": "docs/01-PRD.md §12", "acceptance": "docs/10-ACCEPTANCE.md B6"},
    {"id": "V1-AI-FAIL-CREATE", "requirement": "AI 故障时工单仍创建成功进入人工队列", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "prd": "docs/01-PRD.md §8", "acceptance": "docs/10-ACCEPTANCE.md B4"},
    {"id": "V1-AUTO-DISPATCH-FILTER", "requirement": "自动派单硬过滤+评分+可追溯理由，禁止 LLM 点名", "impl": "页面存在但无真实接线", "ui_or_api": "API", "prd": "docs/01-PRD.md §9", "acceptance": "docs/10-ACCEPTANCE.md B3"},
    {"id": "V1-NTF-EVENTS", "requirement": "创建/派单/接单/状态/SLA/完成/公告通知事件覆盖", "impl": "部分实现", "ui_or_api": "API", "prd": "docs/01-PRD.md §14"},
    {"id": "V1-GOVERNANCE-OPS", "requirement": "健康检查、结构化日志、request_id、上传校验、幂等、并发接单", "impl": "不属于小程序页面、应单独后端验证", "ui_or_api": "API", "acceptance": "docs/10-ACCEPTANCE.md B8"},
]

FIX1_OVERLAY_IDS = {
    "AUTH-RESET",
    "AUTH-RESET-REVOKE",
    "AUTH-RESET-RELOGIN",
    "STAFF-CLAIM-DUP",
    "AUTH-SMS-REUSE",
    "AUTH-SMS-EXPIRE",
    "INCIDENT-UI",
    "STAFF-DAILY-PAGE",
}
PRODUCT_PATCH = "b1458bf54a7cd66d8c4024c12f5b8f2125a148ea"

MATRIX["items"].extend(
    [
        {"id": "MGR-TENANT-HOME", "requirement": "租户管理员首页入口", "role": "tenant_admin", "page": "pages/home/home", "impl": "已实现可测试", "ui_or_api": "UI"},
        {"id": "RES-AFTER-APPROVE", "requirement": "批准后刷新可见有效房屋关系", "role": "resident", "page": "pages/home/home", "impl": "已实现可测试", "ui_or_api": "UI"},
        {"id": "WO-REENTER", "requirement": "重新进入详情状态人员房屋历史一致", "role": "resident", "page": "pages/work-orders/detail", "impl": "已实现可测试", "ui_or_api": "UI"},
        {"id": "STAFF-DAILY-PAGE", "requirement": "物业员工状态页", "role": "property_admin", "page": "pages/supervisor/staff", "impl": "已实现可测试", "ui_or_api": "UI"},
        {"id": "AUTH-RESET-RELOGIN", "requirement": "重置后用新密码进入首页", "role": "resident", "page": "pages/home/home", "impl": "已实现可测试", "ui_or_api": "UI"},
    ]
)
seen = set()
unique_items = []
for item in MATRIX["items"]:
    if item["id"] in seen:
        continue
    seen.add(item["id"])
    unique_items.append(item)
MATRIX["items"] = unique_items
MATRIX["extra_v1_from_prd_acceptance"] = EXTRA_V1
MATRIX["denominator_includes_extra_v1"] = True
MATRIX["fix1_run_id"] = "mp-cfull-20260919-fix1"
MATRIX["product_patch"] = PRODUCT_PATCH
(ROOT / "coverage-matrix.json").write_text(json.dumps(MATRIX, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

by_id = {row["id"]: dict(row) for row in FIRST["results"]}
for row in FIX1["results"]:
    previous = by_id.get(row["id"])
    merged = dict(row)
    if previous:
        merged["baseline_status"] = previous.get("status")
        merged["baseline_run_id"] = FIRST.get("run_id")
    if row["id"] in FIX1_OVERLAY_IDS:
        merged["code_sha_baseline"] = "ab0b8822b34ab26c4c2960dde00c157c3b141725"
        merged["code_sha_product"] = PRODUCT_PATCH
        merged["product_patch"] = "invalid-invite-400 + DevTools FIX-1"
    by_id[row["id"]] = merged

# AUTH-RESET: first named screenshot lagged; login is proved by later unique shots.
if "AUTH-RESET" in by_id:
    by_id["AUTH-RESET"]["status"] = "PASS"
    by_id["AUTH-RESET"]["evidence"] = [
        "mp-cfull-20260919-fix1-reset-old-password.png",
        "mp-cfull-20260919-fix1-reset-back-login-rendered.png",
        "mp-cfull-20260919-fix1-probe-login.png",
    ]
    by_id["AUTH-RESET"]["note"] = (
        "waitPath was login. File mp-cfull-20260919-fix1-reset-back-login.png lagged on the reset page and is not used as UI proof. "
        "Rendered login is mp-cfull-20260919-fix1-reset-old-password.png (phones redacted in public copy)."
    )

if "AUTH-PASSWORD-POLICY" not in by_id:
    by_id["AUTH-PASSWORD-POLICY"] = {
        "id": "AUTH-PASSWORD-POLICY",
        "role": "resident",
        "page": "pages/password-reset/password-reset",
        "status": "PARTIAL",
        "expected": "不强制大写；最少 8 字符；禁止纯数字/纯小写字母",
        "actual": "no dedicated policy-only case; AUTH-RESET-MISMATCH recorded 偏弱 then 两次密码不一致; page copy includes 不强制大写",
        "evidence": ["mp-cfull-20260919-1-reset-mismatch.png"],
        "code_sha_product": "ab0b8822b34ab26c4c2960dde00c157c3b141725",
        "note": "Not a new product decision. Do not treat as a dedicated FIX-1 PASS.",
    }

combined_results = list(by_id.values())
counts = Counter(row.get("status") for row in combined_results)
extra_status = {
    "V1-RES-RELATION-HISTORY": "NOT_IMPLEMENTED",
    "V1-RES-APPEND": "NOT_IMPLEMENTED",
    "V1-RES-CANCEL": "NOT_IMPLEMENTED",
    "V1-RES-RATING": "NOT_IMPLEMENTED",
    "V1-STAFF-REJECT": "NOT_IMPLEMENTED",
    "V1-STAFF-ESCALATE": "NOT_IMPLEMENTED",
    "V1-RECEIPT-COMPLETE": "NOT_IMPLEMENTED",
    "V1-RECEIPT-HISTORY": "NOT_IMPLEMENTED",
    "V1-MGR-EXCEPTION": "NOT_IMPLEMENTED",
    "V1-INCIDENT-AGGREGATE": "NOT_IMPLEMENTED",
    "V1-ANN-TARGET": "NOT_IMPLEMENTED",
    "V1-ANN-EDIT-HISTORY": "NOT_IMPLEMENTED",
    "V1-SLA-CONFIG": "NOT_IMPLEMENTED",
    "V1-NTF-RETRY": "NOT_IMPLEMENTED",
    "V1-GOVERNANCE-IDOR": "NOT_IMPLEMENTED",
    "V1-WORKER-ASYNC": "BLOCKED_ENV",
    "V1-BACKUP-RESTORE": "NOT_IMPLEMENTED",
    "V1-PERF-RELEASE": "BLOCKED_ENV",
    "V1-HOUSE-ARCHIVE-UI": "NOT_IMPLEMENTED",
    "V1-REAL-ASR-VLM": "BLOCKED_ENV",
    "V1-AUTH-LOCKOUT": "NOT_IMPLEMENTED",
    "V1-EMBEDDING-RAG": "BLOCKED_ENV",
    "V1-AI-FAIL-CREATE": "NOT_IMPLEMENTED",
    "V1-AUTO-DISPATCH-FILTER": "NOT_IMPLEMENTED",
    "V1-NTF-EVENTS": "NOT_IMPLEMENTED",
    "V1-GOVERNANCE-OPS": "NOT_IMPLEMENTED",
}
for item_id, status in extra_status.items():
    if item_id not in by_id:
        combined_results.append(
            {
                "id": item_id,
                "status": status,
                "role": "mixed",
                "page": None,
                "expected": next(row["requirement"] for row in EXTRA_V1 if row["id"] == item_id),
                "actual": "not delivered in current mini-program build",
                "evidence": [],
                "code_sha_product": "ab0b8822b34ab26c4c2960dde00c157c3b141725",
                "note": "Full V1 denominator item; not removed as N/A",
            }
        )
counts = Counter(row.get("status") for row in combined_results)

req_by_id = {item["id"]: item for item in MATRIX["items"]}
for extra in EXTRA_V1:
    req_by_id.setdefault(extra["id"], extra)
synced = []
for row in combined_results:
    base = dict(req_by_id.get(row["id"], {}))
    base["id"] = row["id"]
    base["requirement"] = base.get("requirement") or row.get("expected") or row["id"]
    base["role"] = base.get("role") or row.get("role")
    if "page" not in base:
        base["page"] = row.get("page")
    base["impl"] = base.get("impl") or "from_results"
    base["ui_or_api"] = base.get("ui_or_api") or "UI"
    base["result_status"] = row.get("status")
    synced.append(base)
MATRIX["items"] = synced
MATRIX["extra_v1_from_prd_acceptance"] = EXTRA_V1
MATRIX["denominator_ids"] = [row["id"] for row in combined_results]
MATRIX["denominator_count"] = len(combined_results)
assert {item["id"] for item in MATRIX["items"]} == {row["id"] for row in combined_results}
(ROOT / "coverage-matrix.json").write_text(json.dumps(MATRIX, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

hashes = {}
for png in sorted(ROOT.glob("mp-cfull-20260919-fix1*.png")):
    hashes[png.name] = hashlib.sha256(png.read_bytes()).hexdigest()

combined = {
    "task_id": "MP-CURRENT-FULL-REGRESSION-001",
    "related": "AUTH-IDENTITY-AI-001-C",
    "baseline_run_id": FIRST.get("run_id"),
    "fix1_run_id": FIX1.get("run_id"),
    "code_sha_product_patch": PRODUCT_PATCH,
    "automator_ws": {
        "reset_segment": "ws://127.0.0.1:9434",
        "claim_onward_segment": "ws://127.0.0.1:9435",
        "note": "cli auto --auto-port; 9420/9433 are not this FIX-1 requirement",
    },
    "revision": "20260919_0009",
    "provider_mode": FIX1.get("provider_mode"),
    "objects": FIRST.get("objects"),
    "results": combined_results,
    "counts": dict(counts),
    "total": len(combined_results),
    "headline": "回归完成，仍有缺口",
}
(ROOT / "combined-results.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(ROOT / "screenshot-hashes-fix1.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")

ui_pass_ids = [row["id"] for row in combined_results if row.get("status") == "PASS"]
summary = {
    "task_id": "MP-CURRENT-FULL-REGRESSION-001",
    "related": "AUTH-IDENTITY-AI-001-C",
    "headline": "回归完成，仍有缺口",
    "not_claimed": ["Production V1", "Day 4 PASS", "real SMS", "self-hosted AI", "isolated Worker"],
    "baseline_run_id": "mp-cfull-20260919-1",
    "fix1_run_id": "mp-cfull-20260919-fix1",
    "code_sha_baseline": "ab0b8822b34ab26c4c2960dde00c157c3b141725",
    "product_fix": "invalid employee invite no longer returns 401 (400 INVALID_INPUT) so claim page can show the business error",
    "api": "http://127.0.0.1:8083/api/v1",
    "revision": "20260919_0009",
    "database": "127.0.0.1:3307/property_ai_auth001_test",
    "code_sha_product_patch": PRODUCT_PATCH,
    "automator_ws": {
        "reset_segment": "ws://127.0.0.1:9434",
        "claim_onward_segment": "ws://127.0.0.1:9435",
        "note": "cli auto --auto-port; 9420/9433 are not this FIX-1 requirement",
    },
    "devtools_http": "http://127.0.0.1:24540",
    "api_pid_now": 4596,
    "counts_current_pages_plus_v1_gaps": dict(counts),
    "total_current_pages_plus_v1_gaps": len(combined_results),
    "first_round_counts_kept": {
        "PASS": 28,
        "FAIL": 1,
        "PARTIAL": 6,
        "BLOCKED_ENV": 1,
        "NOT_IMPLEMENTED": 15,
        "NOT_RUN": 4,
        "total": 55,
        "note": "Do not back-date FIX-1 PASS onto ab0b882",
    },
    "fix1_overlays": {
        "AUTH-RESET": "PASS on unique login shots; stale reset-back-login.png not used as proof",
        "AUTH-RESET-REVOKE": "PASS UI old-password + sidecar access/refresh 401",
        "AUTH-RESET-RELOGIN": "PASS",
        "STAFF-CLAIM-DUP": "FAIL on ab0b882 (401 kicked to login); PASS after 400 fix",
        "AUTH-SMS-REUSE": "PASS",
        "AUTH-SMS-EXPIRE": "PARTIAL (no 300s UI wait; isolated API remains)",
        "INCIDENT-UI": "PARTIAL list/empty only",
        "STAFF-DAILY-PAGE": "PASS",
    },
    "provider_modes": {
        "password": "real Argon2id on 0009",
        "sms": "development inbox sidecar",
        "ai": "Fake / development",
        "self_host_ai": "not running",
        "storage": "development in-process memory (cleared when 8083 restarted for the claim 400 fix)",
        "worker": "no isolated auth001 worker; analyze via in-process Fake API",
    },
    "ui_walked_vs_api_only": {
        "ui_pass_examples": ui_pass_ids[:20],
        "api_only_examples": ["AUTH-PASSWORD-CHANGE", "STAFF-INVITE-PAGE", "STAFF-DEPT-SKILL-UI", "KNOWLEDGE-INGEST-UI"],
    },
    "objects": FIRST.get("objects"),
    "accounts_public": "reports/mp-current-full-regression/accounts-public.json",
    "evidence_dir": "reports/mp-current-full-regression/",
    "codex_review": "reports/codex/MP-CURRENT-FULL-REGRESSION-001-FIX-1-review.json",
}
(ROOT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"total": len(combined_results), "counts": dict(counts)}, ensure_ascii=False))

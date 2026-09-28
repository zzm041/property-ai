"""Offline revalidation of immutable candidate-05 outputs; performs no model or business IO."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import site
import sys

ROOT = Path(__file__).resolve().parents[3]
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.domain.observed_event import build_evidence_catalog  # noqa: E402
from app.service.analysis import AnalysisService  # noqa: E402

RUN = ROOT / "reports" / "real-ai-integration-027" / "diagnostic-r3-27b" / "candidate-05"
OUTPUT = RUN / "offline-revalidation.json"
SOURCES = [
    ROOT / "backend" / "app" / "domain" / "observed_event.py",
    ROOT / "backend" / "app" / "service" / "analysis.py",
    ROOT / "backend" / "app" / "domain" / "ai.py",
    ROOT / "backend" / "app" / "provider" / "vision" / "__init__.py",
]


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load(name: str) -> dict:
    return json.loads((RUN / name).read_text(encoding="utf-8"))


def revalidate(row: dict, service: AnalysisService) -> dict:
    item = row["input"]
    report = item.get("paired_report") if row["id"].startswith("PHOTO-") else item.get("report")
    vision = ""
    if row["id"].startswith("PHOTO-"):
        vision_stage = next(stage for stage in row["stages"] if stage["kind"] == "VLM")
        vision = vision_stage["provider_result"][0] or ""
    catalog = build_evidence_catalog(report_text=report, asr_text=None, vision_text=vision)
    mapped, error = service._validate_payload(row.get("raw_payload"), evidence_catalog=catalog)
    return {
        "id": row["id"],
        "raw_payload_sha256": sha256(
            json.dumps(row.get("raw_payload"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "valid": mapped is not None and error is None,
        "error_code": error,
        "effective": None if mapped is None else {
            "category": mapped.category.value,
            "required_skill": mapped.required_skill,
            "risk": mapped.risk.value,
            "low_confidence": mapped.low_confidence,
            "dispatch_fields_applied": mapped.dispatch_fields_applied,
            "event_trace": mapped.event_trace,
        },
    }


def main() -> None:
    assert not OUTPUT.exists(), "Offline result is immutable"
    fixed = load("fixed.json")
    continuation = load("fixed-continuation.json")
    novel = load("novel.json")
    fixed_rows = fixed["results"] + continuation["results"]
    assert len(fixed_rows) == 28 and len({row["id"] for row in fixed_rows}) == 28
    assert len(novel["results"]) == 4
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")
    rows = [revalidate(row, service) for row in fixed_rows + novel["results"]]
    result = {
        "task_id": "REAL-AI-INTEGRATION-027",
        "mode": "OFFLINE_REVALIDATION_NO_MODEL_BUSINESS_DATABASE_OR_QUEUE_IO",
        "at_utc": datetime.now(UTC).isoformat(),
        "model": fixed["model"],
        "model_digest": fixed["model_digest"],
        "prompt_version": fixed["prompt_version"],
        "vision_prompt_version": fixed["vision_prompt_version"],
        "input_runs": {
            name: {"path": str(RUN / name), "sha256": digest(RUN / name)}
            for name in ("fixed.json", "fixed-continuation.json", "novel.json")
        },
        "current_source_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in SOURCES},
        "fixed_case_count": 28,
        "novel_case_count": 4,
        "valid_count": sum(row["valid"] for row in rows),
        "invalid_count": sum(not row["valid"] for row in rows),
        "results": rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("fixed_case_count", "novel_case_count", "valid_count", "invalid_count")}))


if __name__ == "__main__":
    main()

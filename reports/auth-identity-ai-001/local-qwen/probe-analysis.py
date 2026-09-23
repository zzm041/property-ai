"""Real model + existing prompt/validation; deliberately NO DB/Worker/UI claim."""
from pathlib import Path
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import argparse
import json
import runpy
import site
import sys
import time

ROOT = Path(r"D:\property-ai")
OUT = Path(__file__).parent
site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings
from app.domain.ai import WorkOrderAnalysisOutput, map_analysis_output
from app.provider.factory import build_llm_provider, build_vlm_provider
from app.provider.storage import DevelopmentStorageProvider
from app.service.analysis import AnalysisService, AnalysisSnapshot

h = runpy.run_path(str(OUT / "probe-model.py"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--round', type=int, choices=(1, 2), default=1)
    round_number = parser.parse_args().round
    output = OUT / ("probe-analysis.json" if round_number == 1 else "probe-analysis-round2.json")
    assert not output.exists(), "Do not repeat existing real inference evidence"
    downloaded = json.loads((OUT / "download-result.json").read_text())
    assert downloaded["hashes_verified"]
    settings = Settings(_env_file=None, app_env="test", llm_provider="production", vlm_provider="production",
                        llm_base_url=h["BASE"] + "/v1", vlm_base_url=h["BASE"] + "/v1",
                        llm_model=h["MODEL"], vlm_model=h["MODEL"],
                        llm_api_key="local-loopback-no-auth", vlm_api_key="local-loopback-no-auth",
                        llm_disable_thinking=True, vlm_disable_thinking=True,
                        llm_max_output_tokens=512, vlm_max_output_tokens=256,
                        llm_timeout_seconds=60, vlm_timeout_seconds=60)
    evidence = {"task_id": "SELFHOST-AI-LOCAL-001", "mode": "real Qwen via existing factory/Providers/AnalysisService prompt; no DB/queue/Worker/UI",
                "model": h["MODEL"], "manifest_sha256": downloaded["manifest_sha256"],
                "settings_boundary": "production selects real HTTP adapter, not production environment; APP_ENV=test; API/Worker settings unchanged",
                "started_at_utc": datetime.now(UTC).isoformat(), "round": round_number, "results": []}

    def save():
        output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    try:
        baseline = h["resource_sample"]()
        evidence["baseline"] = baseline
        if baseline["ram_free_mib"] < 4500 or baseline["gpu_free_mib"] < 6000:
            evidence["status"] = "RESOURCE_HEADROOM_BLOCKED"
            return
        storage = DevelopmentStorageProvider()
        blue = (OUT / "calibration-blue.png").read_bytes()
        image = storage.register_object(mime_type="image/png", size_bytes=len(blue), tenant_id=701, property_id=702)
        storage.put_object(image.storage_key, blue, "image/png")
        started = time.perf_counter()
        if round_number == 1:
            insight = build_vlm_provider(settings, storage).inspect_image(storage_key=image.storage_key, mime_type="image/png")
            evidence["vision_adapter"] = {"insight": asdict(insight), "elapsed_seconds": round(time.perf_counter() - started, 3),
                                          "input_sha256": hashlib.sha256(blue).hexdigest(), "boundary": "same calibration image through actual storage/VLM adapter; no repair photo or business upload claim"}
        else:
            evidence['vision_adapter_reused_from'] = 'probe-analysis.json; unchanged VLM path, not rerun'
        llm = build_llm_provider(settings)
        # Exercise only the existing prompt/provider path. Capture call logs in memory
        # instead of pretending a real persistent AiCallLog or task was created.
        service = AnalysisService.__new__(AnalysisService)
        service._llm = llm
        service._clock = lambda: datetime.now(UTC)
        logs = []
        service._write_call_log = lambda *args, **kwargs: logs.append(kwargs)
        cases = [
            ("electrical", "厨房插座没有电，其他房间照明正常，没有冒烟，请维修。", 'ELECTRICAL', 'ELECTRICAL', {'none', 'electric'}),
            ("plumbing", "卫生间水龙头一直滴水，关紧后仍漏水，请维修。", 'PLUMBING', 'PLUMBING', {'none', 'flooding'}),
            ("insufficient", "帮忙看看，有点问题，具体在哪里我也不清楚。", 'OTHER', 'OTHER', {'none', 'other'}),
        ]
        if round_number == 2:
            cases.extend([
                ('holdout-cleaning', '楼道里有散落的纸箱和垃圾，需要清扫，没有其他异常。', 'CLEANING', 'CLEANING', {'none'}),
                ('holdout-hvac', '空调能开机，吹出来的风不凉，没有漏水或异味，请检修。', 'HVAC', 'HVAC', {'none'}),
                ('holdout-plumbing', '洗手盆下方的排水接头缓慢渗水，地面有少量水滴，旁边没有电器。', 'PLUMBING', 'PLUMBING', {'none', 'flooding'}),
            ])
        for name, report, expected_category, expected_skill, risks in cases:
            snapshot = AnalysisSnapshot(work_order_id=0, tenant_id=701, property_id=702, house_id=703, building_id=704,
                analysis_id=0, analysis_version=1, work_order_version=1, input_hash=hashlib.sha256(report.encode()).hexdigest(),
                text=report, existing_asr_text=None, images=(), audio=(), context={"scope": "synthetic probe only"})
            started = time.perf_counter()
            payload, error, status = service._run_llm(snapshot, asr_text=None, vision_text=None, request_id="local-qwen-probe-" + name, attempt=1)
            item = {"case": name, "report": report, "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error_code": error, "call_status": status, "raw_payload": payload, "token_usage": getattr(payload, "token_usage", None)}
            if payload is not None:
                try:
                    validated = WorkOrderAnalysisOutput.model_validate(payload)
                    mapped = map_analysis_output(validated)
                    item["mapped"] = asdict(mapped)
                    item["schema_valid"] = True
                    item['expected'] = {'category': expected_category, 'skill': expected_skill, 'allowed_risks': sorted(risks)}
                    item["expectation_met"] = (mapped.category.value == expected_category and mapped.required_skill == expected_skill
                                               and mapped.risk.value in risks and (name != "insufficient" or mapped.low_confidence))
                except Exception as exc:
                    item["schema_valid"] = False
                    item["validation_error_type"] = type(exc).__name__
            evidence["results"].append(item)
            save()
            print(json.dumps({k: item.get(k) for k in ("case", "elapsed_seconds", "schema_valid", "expectation_met", "error_code")}), flush=True)
        evidence["status"] = "PASS_BOUNDED_PROVIDER_PROMPT_PROBE" if all(r.get("expectation_met") for r in evidence["results"]) else "NOT_ALL_PROBES_PASSED"
        evidence["call_log_capture"] = logs
        evidence["final_resource_sample"] = h["resource_sample"]()
    except Exception as exc:
        evidence["status"] = "PROBE_ERROR"
        evidence["error_type"] = type(exc).__name__
    finally:
        save()
        print(json.dumps({"status": evidence["status"], "completed_cases": len(evidence["results"])}), flush=True)


if __name__ == "__main__":
    main()

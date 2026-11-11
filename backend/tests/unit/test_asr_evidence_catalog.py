"""t166: the analysis must read transcripts, not the transcript envelope.

`_run_all_asr` stores a JSON array of per-audio transcripts because the
work-order API surfaces it verbatim. Sentence-splitting that envelope handed the
model ``asr:1`` (cut mid-string) plus ``asr:2`` (``error_code``/``source``
plumbing), so the voice chain produced evidence the model could not cite.
"""

from __future__ import annotations

import json

from app.domain.ai.rules import build_evidence_catalog


def _transcripts(*rows: dict[str, object]) -> str:
    return json.dumps(list(rows), ensure_ascii=False)


def test_asr_envelope_becomes_plain_transcript_lines() -> None:
    asr_text = _transcripts(
        {"audio_index": 1, "text": "厨房的水管漏水了，地板已经积水。", "error_code": None, "source": "provider"},
        {"audio_index": 2, "text": "请尽快安排维修师傅上门。", "error_code": None, "source": "provider"},
    )

    catalog = build_evidence_catalog(report_text="厨房水管漏水", asr_text=asr_text, vision_text=None)

    assert catalog["asr:1"]["text"] == "厨房的水管漏水了，地板已经积水。"
    assert catalog["asr:2"]["text"] == "请尽快安排维修师傅上门。"
    assert "asr:3" not in catalog
    for entry in catalog.values():
        assert "error_code" not in str(entry["text"])
        assert "audio_index" not in str(entry["text"])


def test_asr_span_matches_its_own_segment() -> None:
    asr_text = _transcripts({"audio_index": 1, "text": "楼道灯不亮，晚上看不清台阶。", "error_code": None})

    catalog = build_evidence_catalog(report_text=None, asr_text=asr_text, vision_text=None)

    entry = catalog["asr:1"]
    text = str(entry["text"])
    span = entry["span"]
    assert isinstance(span, list) and len(span) == 2
    assert text[span[0] : span[1]] == text
    assert text.startswith("楼道灯不亮")


def test_asr_envelope_skips_failed_audio_only_rows() -> None:
    asr_text = _transcripts(
        {"audio_index": 1, "text": None, "error_code": "AI.TIMEOUT", "source": "provider"},
        {"audio_index": 2, "text": "插座面板发黑。", "error_code": None, "source": "checkpoint"},
    )

    catalog = build_evidence_catalog(report_text=None, asr_text=asr_text, vision_text=None)

    assert list(catalog) == ["asr:1"]
    assert catalog["asr:1"]["text"] == "插座面板发黑。"


def test_plain_asr_text_keeps_the_sentence_split() -> None:
    asr_text = "厨房水管漏水。地板已经积水。"

    catalog = build_evidence_catalog(report_text=None, asr_text=asr_text, vision_text=None)

    assert [catalog[key]["text"] for key in ("asr:1", "asr:2")] == ["厨房水管漏水。", "地板已经积水。"]


def test_legacy_json_that_is_not_the_envelope_still_splits() -> None:
    asr_text = json.dumps({"note": "不是转写信封"}, ensure_ascii=False)

    catalog = build_evidence_catalog(report_text=None, asr_text=asr_text, vision_text=None)

    assert list(catalog) == ["asr:1"]
    assert catalog["asr:1"]["text"] == asr_text

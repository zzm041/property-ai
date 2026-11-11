"""Line-delimited resident facts must survive source segmentation exactly."""
import pytest

from app.domain.ai import build_evidence_catalog


@pytest.mark.parametrize("source", ["report", "asr"])
@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("lines", [
    ["请开通门禁。", "插座正在冒烟", "谢谢。"],
    ["请开通门禁", "另外设备有异常", "谢谢"],
    ["  第一行没有句号  ", "\t第二行有句号。", "", "第三行也没有"],
    ["水龙头滴水，原因未知", "不要直接上门", "请先留言"],
])
def test_every_nonempty_line_and_exact_span_are_preserved(source, separator, lines):
    text = separator.join(lines)
    catalog = build_evidence_catalog(**{f"{s}_text": text if s == source else None
                                       for s in ("report", "asr", "vision")})
    expected = [line.strip() for line in lines if line.strip()]
    assert [row["text"] for row in catalog.values()] == expected
    assert list(catalog) == [f"{source}:{i + 1}" for i in range(len(expected))]
    previous_end = 0
    for row in catalog.values():
        start, end = row["span"]
        assert start >= previous_end
        assert text[start:end] == row["text"]
        assert not text[previous_end:start].strip()
        previous_end = end
    assert not text[previous_end:].strip()


def test_existing_single_line_sentence_ids_and_offsets_do_not_change():
    catalog = build_evidence_catalog(report_text="柜门合不上。水龙头滴水！", asr_text=None, vision_text=None)
    assert catalog == {
        "report:1": {"source": "report", "text": "柜门合不上。", "span": [0, 6]},
        "report:2": {"source": "report", "text": "水龙头滴水！", "span": [6, 12]},
    }

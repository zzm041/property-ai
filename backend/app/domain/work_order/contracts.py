"""Response contracts the work order domain offers to the AI providers."""

from copy import deepcopy


VISION_ANALYSIS_PROMPT_VERSION = "repair-photo-v7-confidence-scale"


_VISION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "repair_photo_insight",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["description", "labels", "confidence"],
            "properties": {
                "description": {"type": "string", "minLength": 1, "maxLength": 200},
                "labels": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
    },
}


def vision_analysis_response_format() -> dict[str, object]:
    """Return an isolated copy used by both provider and local adapter guards."""

    return deepcopy(_VISION_RESPONSE_FORMAT)

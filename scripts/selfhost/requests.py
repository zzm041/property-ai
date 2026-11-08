"""Protocol limits only; business prompt semantics stay in backend services."""
import base64
import binascii
import json
import math


def texts(value, limit=16, length=4000):
    return isinstance(value, list) and 1 <= len(value) <= limit and all(
        isinstance(item, str) and 0 < len(item.strip()) <= length for item in value)


def validate(kind, value, model):
    if not isinstance(value, dict):
        raise ValueError()
    if kind == "embedding":
        if set(value) != {"model", "input"} or value["model"] != "bge-m3" or not texts(value["input"]):
            raise ValueError()
        return value["input"]
    if kind == "reranker":
        if set(value) != {"model", "query", "documents"} or value["model"] != "bge-reranker-v2-m3" or not texts([value["query"]], length=1000) or not texts(value["documents"]):
            raise ValueError()
        return {"query": value["query"], "documents": value["documents"]}
    allowed = {"model", "messages", "temperature", "max_tokens", "stream", "response_format", "reasoning_effort"}
    if set(value) - allowed or value.get("model") != model or value.get("stream", False) is not False:
        raise ValueError()
    if value.get("reasoning_effort", "none") != "none":
        raise ValueError()
    tokens = value.get("max_tokens", 4096)
    temperature = value.get("temperature", 0)
    if type(tokens) is not int or not 1 <= tokens <= 4096 or type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 1:
        raise ValueError()
    messages = value.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError()
    text_size = image_size = image_count = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] not in ("system", "user", "assistant"):
            raise ValueError()
        content = message["content"]
        if isinstance(content, str):
            text_size += len(content)
            continue
        if not isinstance(content, list) or not 1 <= len(content) <= 16:
            raise ValueError()
        for part in content:
            if not isinstance(part, dict):
                raise ValueError()
            if part.get("type") == "text" and set(part) == {"type", "text"} and isinstance(part["text"], str):
                text_size += len(part["text"])
            elif part.get("type") == "image_url" and set(part) == {"type", "image_url"}:
                image = part["image_url"]
                if not isinstance(image, dict) or set(image) - {"url", "detail"}:
                    raise ValueError()
                url = image.get("url", "")
                if not isinstance(url, str) or not url.startswith(("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
                    raise ValueError()
                try:
                    decoded = base64.b64decode(url.split(",", 1)[1], validate=True)
                except (ValueError, binascii.Error):
                    raise ValueError() from None
                if not decoded:
                    raise ValueError()
                image_size += len(decoded)
                image_count += 1
            else:
                raise ValueError()
    if text_size > 64000 or image_size > 10 * 1024 * 1024 or image_count > 8 or text_size + image_size == 0:
        raise ValueError()
    if "response_format" in value:
        fmt = value["response_format"]
        if not isinstance(fmt, dict) or fmt.get("type") not in ("json_object", "json_schema") or len(json.dumps(fmt)) > 65536:
            raise ValueError()
    # No generated answer, prompt rewrite, employee selection or business fallback here.
    return {**value, "max_tokens": tokens, "reasoning_effort": "none"}

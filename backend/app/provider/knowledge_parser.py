"""Deterministic knowledge source parsers. OCR is out of V1."""


class KnowledgeParseError(ValueError):
    """Source bytes could not be turned into text."""


class KnowledgeParser:
    def parse(self, *, filename: str | None, content: bytes | str) -> str:
        raise NotImplementedError


class TextKnowledgeParser(KnowledgeParser):
    """UTF-8 text, Markdown, and FAQ bodies."""

    def parse(self, *, filename: str | None, content: bytes | str) -> str:
        if isinstance(content, str):
            text = content
        else:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise KnowledgeParseError("Source is not valid UTF-8 text") from exc
        name = (filename or "").casefold()
        if name.endswith(".pdf"):
            raise KnowledgeParseError("PDF parser is not enabled in V1")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise KnowledgeParseError("Source text is empty")
        return normalized

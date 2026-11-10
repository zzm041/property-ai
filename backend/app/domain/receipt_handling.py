"""A receipt author's participation declaration, never inferred job credit."""

HANDLING_LABELS = {'SELF': '本人独立处理', 'ASSISTED': '本人参与协作', 'EXTERNAL': '由支援人员处理'}


def normalize_handling(mode: str | None, note: str | None) -> tuple[str | None, str | None]:
    if mode is not None and (not isinstance(mode, str) or mode not in HANDLING_LABELS):
        raise ValueError('handling_mode is invalid')
    if note is not None and (not isinstance(note, str) or len(note) > 255):
        raise ValueError('handling_note must be at most 255 characters')
    cleaned = note.strip() or None if note is not None else None
    if mode is None and cleaned is not None:
        raise ValueError('handling_mode is required with handling_note')
    if mode in {'ASSISTED', 'EXTERNAL'} and cleaned is None:
        raise ValueError('handling_note is required for assisted or external work')
    return mode, cleaned


def handling_reason(mode: str | None, note: str | None) -> str | None:
    if mode is None:
        return None
    return f'实际处理（提交者自报）：{HANDLING_LABELS[mode]}' + (f'；{note}' if note else '')

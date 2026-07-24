from typing import Any, Optional


def to_int(value: Any, or_default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (ValueError, TypeError):
        return or_default


def page_size(value: Any, *, default: int = 12, maximum: int = 48) -> int:
    parsed = to_int(value, or_default=default)
    return max(1, min(maximum, parsed))

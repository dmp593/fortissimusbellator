from typing import Any, Optional


def to_int(value: Any, or_default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (ValueError, TypeError):
        return or_default

"""Safe path handling for frontend static files."""

from pathlib import Path
from typing import Optional, Union


def resolve_static_file(static_root: Union[str, Path], requested_path: str) -> Optional[Path]:
    """Return an existing regular file only when it resolves below ``static_root``."""
    root = Path(static_root).resolve()
    candidate = (root / requested_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None

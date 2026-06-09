"""In-memory cache of RA's console name → ID mapping.

Loaded once at startup from `API_GetConsoleIDs.php`. Used by embed renderers
to render `Console: [Wii](https://retroachievements.org/system/16)` style links
without having to plumb IDs through every event type.
"""

from __future__ import annotations

_id_by_name: dict[str, int] = {}


def set_consoles(mapping: dict[str, int]) -> None:
    global _id_by_name
    _id_by_name = dict(mapping)


def console_id(name: str) -> int | None:
    if not name:
        return None
    return _id_by_name.get(name)


def console_url(name: str) -> str | None:
    cid = console_id(name)
    if cid is None:
        return None
    return f"https://retroachievements.org/system/{cid}/games"


def format_console(name: str) -> str:
    """Render the console as a markdown link if we know its ID, else plain text."""
    if not name:
        return ""
    url = console_url(name)
    return f"[{name}]({url})" if url else name

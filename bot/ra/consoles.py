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


def all_console_names() -> list[str]:
    """Sorted list of every console name we know about. Used for autocomplete."""
    return sorted(_id_by_name.keys())


def resolve_console(query: str) -> tuple[str, int] | None:
    """Best-effort match of a free-text console query against the loaded list.

    Returns `(canonical_name, id)` if any console matches case-insensitively,
    else None. Prefers exact (case-insensitive) match, then prefix, then
    substring — and finally a small set of common shorthands so users can
    type "PS2" instead of "PlayStation 2".
    """
    if not query:
        return None
    q = query.strip()
    if not q:
        return None

    q_lower = q.lower()
    # Exact (case-insensitive)
    for name, cid in _id_by_name.items():
        if name.lower() == q_lower:
            return name, cid

    # Common shorthands → canonical RA console names. Kept tiny on purpose;
    # anything beyond this just lets the prefix/substring pass do the work.
    shorthands = {
        "ps1": "playstation",
        "psx": "playstation",
        "ps2": "playstation 2",
        "psp": "playstation portable",
        "snes": "snes/super famicom",
        "nes": "nes/famicom",
        "n64": "nintendo 64",
        "gb": "game boy",
        "gbc": "game boy color",
        "gba": "game boy advance",
        "ds": "nintendo ds",
        "3ds": "nintendo 3ds",
        "md": "mega drive/genesis",
        "genesis": "mega drive/genesis",
        "saturn": "sega saturn",
        "dreamcast": "dreamcast",
        "sms": "master system",
        "gg": "game gear",
        "32x": "sega 32x",
        "cd": "sega cd",
    }
    canonical = shorthands.get(q_lower)
    if canonical is not None:
        for name, cid in _id_by_name.items():
            if name.lower() == canonical:
                return name, cid

    # Prefix
    for name, cid in _id_by_name.items():
        if name.lower().startswith(q_lower):
            return name, cid

    # Substring
    for name, cid in _id_by_name.items():
        if q_lower in name.lower():
            return name, cid

    return None

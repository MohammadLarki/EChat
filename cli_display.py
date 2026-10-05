"""Terminal-only formatting for the interactive chat picker."""

import unicodedata
from typing import Iterable

from telethon.tl.types import Channel, Chat, User


COMPACT_WIDTH = 76


def _chat_kind(dialog) -> str:
    entity = dialog.entity
    if isinstance(entity, User):
        if getattr(entity, "is_self", False):
            return "Personal"
        return "Bot" if getattr(entity, "bot", False) else "Private"
    if isinstance(entity, Chat):
        return "Group"
    if isinstance(entity, Channel):
        return "Channel" if getattr(entity, "broadcast", False) else "Supergroup"
    return "Unknown"


def _cell_width(value: str) -> int:
    width = 0
    for char in value:
        if char == "\u200d" or unicodedata.combining(char):
            continue
        category = unicodedata.category(char)
        if category in {"Mn", "Me", "Cf"}:
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _truncate_cells(value: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _cell_width(value) <= max_width:
        return value
    if max_width == 1:
        return "…"
    result = []
    width = 0
    for char in value:
        char_width = _cell_width(char)
        if width + char_width > max_width - 1:
            break
        result.append(char)
        width += char_width
    return "".join(result).rstrip() + "…"


def dialog_lines(dialogs: Iterable, start: int = 1, width: int = 80):
    """Format dialog rows for the current terminal width."""
    width = max(20, width)
    compact = width < COMPACT_WIDTH
    lines = []
    for index, dialog in enumerate(dialogs, start):
        name = dialog.name or "(unnamed)"
        kind = _chat_kind(dialog)
        if compact:
            prefix = f"[{index}] {kind}"
            lines.append(_truncate_cells(prefix, width))
            lines.append("    " + _truncate_cells(name, width - 4))
        else:
            prefix = f"[{index:>3}] {kind} | "
            lines.append(prefix + _truncate_cells(name, width - _cell_width(prefix)))
    return lines

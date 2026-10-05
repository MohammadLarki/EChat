#!/usr/bin/env python3
"""
Telethon -> Telegram Desktop export JSON serializer.

Every rule here was verified against Telegram Desktop's own export writer
(Telegram/SourceFiles/export/output/export_output_json.cpp and
Telegram/SourceFiles/export/data/export_data_types.cpp, dev branch) and
against the two reference result.json files shipped with this project.

Callback payloads are treated as opaque bytes and never altered:

    MTProto raw bytes -> base64url, no padding -> "dataBase64"

which is exactly what tdesktop does:
    entry.data.toBase64(QByteArray::Base64UrlEncoding
                        | QByteArray::OmitTrailingEquals)
followed by an unconditional "data": "" pair.

This module depends only on the standard library. Telethon objects are
accessed through duck typing (raw TL constructors) so it can be unit-tested
without a Telegram connection.
"""

import base64
import json

# --------------------------------------------------------------------------
# dataBase64 (the critical part)
# --------------------------------------------------------------------------

def encode_callback_data(raw: bytes) -> str:
    """MTProto raw callback bytes -> tdesktop-compatible dataBase64.

    tdesktop: data.toBase64(Base64UrlEncoding | OmitTrailingEquals).
    QByteArray's Base64UrlEncoding only changes the *alphabet* (+/ -> -_);
    padding is omitted via the flag. The bytes themselves are never touched.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError(f"callback data must be bytes, got {type(raw)!r}")
    return base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")


def decode_callback_data(encoded: str) -> bytes:
    """Inverse of encode_callback_data (used by tests and validation)."""
    if not isinstance(encoded, str):
        raise TypeError("dataBase64 must be a str")
    padding = -len(encoded) % 4
    return base64.urlsafe_b64decode(encoded + "=" * padding)


# --------------------------------------------------------------------------
# tdesktop-compatible JSON primitives
# --------------------------------------------------------------------------

# chat type strings used by tdesktop's writeDialogStart / frequent-contacts
CHAT_TYPE_USER = "user"
CHAT_TYPE_PERSONAL = "personal_chat"
CHAT_TYPE_BOT = "bot_chat"
CHAT_TYPE_SAVED = "saved_messages"
CHAT_TYPE_PRIVATE_GROUP = "private_group"
CHAT_TYPE_PRIVATE_SUPERGROUP = "private_supergroup"
CHAT_TYPE_PUBLIC_SUPERGROUP = "public_supergroup"
CHAT_TYPE_PRIVATE_CHANNEL = "private_channel"
CHAT_TYPE_PUBLIC_CHANNEL = "public_channel"

FILE_NOT_INCLUDED = "(File not included. Change data exporting settings to download.)"
FILE_UNAVAILABLE = "(File unavailable, please try again later)"
FILE_TOO_LARGE = "(File exceeds maximum size. Change data exporting settings to download.)"

# Entity TL name -> tdesktop text type string (SerializeText in json.cpp).
# Anything missing falls back to "unknown", exactly like
# messageEntityFormattedDate/messageEntityDiff* do inside tdesktop.
ENTITY_TYPE_NAMES = {
    "MessageEntityUnknown": "unknown",
    "MessageEntityMention": "mention",
    "MessageEntityHashtag": "hashtag",
    "MessageEntityBotCommand": "bot_command",
    "MessageEntityUrl": "link",
    "MessageEntityEmail": "email",
    "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic",
    "MessageEntityCode": "code",
    "MessageEntityPre": "pre",
    "MessageEntityTextUrl": "text_link",
    "MessageEntityMentionName": "mention_name",
    "MessageEntityPhone": "phone",
    "MessageEntityCashtag": "cashtag",
    "MessageEntityUnderline": "underline",
    "MessageEntityStrike": "strikethrough",
    "MessageEntityBlockquote": "blockquote",
    "MessageEntityBankCard": "bank_card",
    "MessageEntitySpoiler": "spoiler",
    "MessageEntityCustomEmoji": "custom_emoji",
    "InputMessageEntityMentionName": "mention_name",
}


def utf16_len(text: str) -> int:
    """Length of text in UTF-16 code units (Telegram entity offsets)."""
    return len(text.encode("utf-16-le")) // 2


def utf16_slice(text: str, start: int, end: int) -> str:
    """Slice by UTF-16 code-unit offsets, honoring surrogate pairs."""
    if start < 0:
        start = 0
    if end < start:
        return ""
    units = text.encode("utf-16-le")
    try:
        return units[2 * start:2 * end].decode("utf-16-le", "replace")
    except UnicodeDecodeError:
        # Should not happen when offsets are even, but never crash the export.
        return ""


def _entity_offset_length(entity) -> tuple:
    """Extract (offset, length) from any TL messageEntity* constructor."""
    if hasattr(entity, "offset") and hasattr(entity, "length"):
        # Telethon's convenience wrapper sets .offset/.length in UTF-16 units
        return int(entity.offset), int(entity.length)
    data = getattr(entity, "data", entity)
    offset = getattr(data, "offset", None) or getattr(data, "voffset", None)
    length = getattr(data, "length", None) or getattr(data, "vlength", None)
    if offset is None or length is None:
        return 0, 0
    if hasattr(offset, "value"):
        offset = offset.value
    if hasattr(length, "value"):
        length = length.value
    return int(offset), int(length)


def _entity_class_name(entity) -> str:
    name = type(entity).__name__
    if name in ENTITY_TYPE_NAMES:
        return name
    # Telethon class names look like MessageEntityBold already; raw TL
    # containers may look like MessageEntityBold as well. Strip nothing.
    return name


def split_text_parts(text: str, entities) -> list:
    """Reproduce tdesktop's Data::ParseText (bug-compatible on purpose).

    Telegram entity offsets are UTF-16 code units, but tdesktop compares
    them (and the final addTextPart) against the UTF-8 BYTE size of the
    message and slices with QString::mid (clamped). For Persian/Arabic/
    emoji texts the byte size exceeds the UTF-16 length, so tdesktop can
    emit an extra EMPTY trailing plain part - visible in the reference
    result.json files as "", {"type": "plain", "text": ""}. We reproduce
    that exactly instead of "fixing" it.
    """
    size = len(text.encode("utf-8"))  # tdesktop: data.v.size() in bytes

    normalized = []
    for entity in entities or []:
        cls = _entity_class_name(entity)
        start, length = _entity_offset_length(entity)
        # tdesktop check (start is unsigned in TL; length > 0):
        if length <= 0 or start + length > size:
            continue
        normalized.append((start, length, cls, entity))
    normalized.sort(key=lambda item: item[0])

    parts = []
    cursor = 0
    for start, length, cls, entity in normalized:
        if start < cursor:
            continue  # overlapping entity -> skip, like tdesktop
        if start > cursor:
            parts.append({
                "type": "plain",
                "text": utf16_slice(text, cursor, start),
            })
        item = {"type": ENTITY_TYPE_NAMES.get(cls, "unknown"),
                "text": utf16_slice(text, start, start + length)}
        extra = _entity_extra(cls, entity)
        if extra:
            item.update(extra)
        parts.append(item)
        cursor = start + length
    if size > cursor:
        # tdesktop's final addTextPart(size): byte size against a UTF-16
        # cursor, sliced with clamping -> may legitimately be "".
        parts.append({"type": "plain", "text": utf16_slice(text, cursor, size)})
    return parts


def _entity_extra(cls: str, entity) -> dict:
    """Additional attribute for text_link/pre/mention_name/custom_emoji."""
    if cls == "MessageEntityTextUrl":
        return {"href": getattr(entity, "url", "") or ""}
    if cls == "MessageEntityPre":
        return {"language": getattr(entity, "language", "") or ""}
    if cls in ("MessageEntityMentionName", "InputMessageEntityMentionName"):
        user_id = getattr(entity, "user_id", None)
        return {"user_id": str(int(user_id)) if user_id else ""}
    if cls == "MessageEntityCustomEmoji":
        doc_id = getattr(entity, "document_id", None)
        return {"document_id": str(int(doc_id)) if doc_id else ""}
    if cls == "MessageEntityBlockquote" and getattr(
            entity, "collapsed", False):
        return {"collapsed": True}
    return {}


def serialize_text_field(parts: list):
    """tdesktop SerializeText: single plain part -> bare string, else array."""
    if not parts:
        return ""
    if len(parts) == 1 and parts[0]["type"] == "plain":
        return parts[0]["text"]
    out = []
    for part in parts:
        if part["type"] == "plain":
            out.append(part["text"])
        else:
            item = {"type": part["type"], "text": part["text"]}
            for key in ("href", "language", "user_id", "document_id",
                        "collapsed"):
                if key in part:
                    item[key] = part[key]
            out.append(item)
    return out


def serialize_text_entities(parts: list) -> list:
    """tdesktop text_entities: same parts, but plain parts are objects too."""
    out = []
    for part in parts:
        item = {"type": part["type"], "text": part["text"]}
        for key in ("href", "language", "user_id", "document_id",
                    "collapsed"):
            if key in part:
                item[key] = part[key]
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Inline keyboards
# --------------------------------------------------------------------------

BUTTON_TYPE_NAMES = {
    "KeyboardButton": "default",
    "KeyboardButtonUrl": "url",
    "KeyboardButtonCallback": "callback",
    "KeyboardButtonRequestPhone": "request_phone",
    "KeyboardButtonRequestLocation": "request_location",
    "KeyboardButtonRequestPoll": "request_poll",
    "KeyboardButtonRequestPeer": "request_peer",
    "KeyboardButtonSwitchInline": "switch_inline",
    "KeyboardButtonGame": "game",
    "KeyboardButtonBuy": "buy",
    "KeyboardButtonUrlAuth": "auth",
    "KeyboardButtonUserProfile": "user_profile",
    "KeyboardButtonWebView": "web_view",
    "KeyboardButtonSimpleWebView": "simple_web_view",
    "KeyboardButtonCopy": "copy_text",
}


class CallbackFailure:
    """One callback payload that could not be preserved byte-for-byte."""

    def __init__(self, chat_id, message_id, row, button, reason):
        self.chat_id = chat_id
        self.message_id = message_id
        self.row = row
        self.button = button
        self.reason = reason

    def __str__(self):
        return (f"chat_id={self.chat_id} message_id={self.message_id} "
                f"row={self.row} button={self.button} ({self.reason})")


def serialize_button(button, *, chat_id=None, message_id=None,
                     row=0, column=0, failures=None):
    """Serialize one inline button, preserving callback bytes exactly.

    Mirrors the button block of SerializeMessage in export_output_json.cpp:
      - text emitted only when non-empty
      - non-empty callback payloads: dataBase64 (base64url, unpadded), then
        unconditional "data": ""; empty payload fields are omitted
      - everything else: "data" carries the raw payload string
      - forward_text / button_id when present
    Unknown button constructors degrade to type "disabled" with text only.
    """
    cls = type(button).__name__
    fields = button
    if cls == "KeyboardInlineButton":
        # Telethon 1.45+ wraps inline actions in raw InlineButtonType TL
        # constructors. Read data from that constructor without transforming it.
        fields = getattr(button, "type", None)
        cls = {
            "InlineButtonTypeCallback": "KeyboardButtonCallback",
            "InlineButtonTypeUrl": "KeyboardButtonUrl",
            "InlineButtonTypeUrlAuth": "KeyboardButtonUrlAuth",
            "InlineButtonTypeWebView": "KeyboardButtonWebView",
            "InlineButtonTypeSwitchInline": "KeyboardButtonSwitchInline",
            "InlineButtonTypeGame": "KeyboardButtonGame",
            "InlineButtonTypeBuy": "KeyboardButtonBuy",
            "InlineButtonTypeUserProfile": "KeyboardButtonUserProfile",
            "InlineButtonTypeCopy": "KeyboardButtonCopy",
            "InlineButtonTypeDisabled": "KeyboardButtonDisabled",
        }.get(type(fields).__name__, type(fields).__name__)
    text = getattr(button, "text", "") or ""
    item = {}

    if cls == "KeyboardButtonUrl":
        item["type"] = "url"
        if text:
            item["text"] = text
        item["data"] = getattr(fields, "url", "") or ""
    elif cls == "KeyboardButtonCallback":
        is_password = bool(getattr(fields, "requires_password", False))
        item["type"] = ("callback_with_password" if is_password
                        else "callback")
        if text:
            item["text"] = text
        raw = getattr(fields, "data", None)
        if isinstance(raw, (bytes, bytearray, memoryview)):
            raw = bytes(raw)
            # Telegram Desktop omits both fields when the payload is empty.
            if raw:
                item["dataBase64"] = encode_callback_data(raw)
                item["data"] = ""
                # invariant check: decode(encode(x)) == x
                if decode_callback_data(item["dataBase64"]) != raw:
                    if failures is not None:
                        failures.append(CallbackFailure(
                            chat_id, message_id, row, column,
                            "round-trip verification failed"))
                    item.pop("dataBase64", None)
                    item.pop("data", None)
        else:
            # Telethon keeps bytes; a non-bytes payload would mean a library
            # change. Never guess: record the failure, keep the button.
            if failures is not None:
                failures.append(CallbackFailure(
                    chat_id, message_id, row, column,
                    f"raw callback bytes unavailable (got {type(raw).__name__})"))
            # Do not emit a misleading encoding when raw bytes are unavailable.
    elif cls == "KeyboardButtonUrlAuth":
        item["type"] = "auth"
        if text:
            item["text"] = text
        item["url"] = getattr(fields, "url", "") or ""
        fwd = getattr(fields, "fwd_text", None)
        if fwd:
            item["forward_text"] = fwd
        bid = getattr(fields, "button_id", None)
        if bid:
            item["button_id"] = int(bid)
    elif cls in ("KeyboardButtonWebView", "KeyboardButtonSimpleWebView"):
        item["type"] = ("web_view" if cls == "KeyboardButtonWebView"
                        else "simple_web_view")
        if text:
            item["text"] = text
        item["data"] = getattr(fields, "url", "") or ""
    elif cls == "KeyboardButtonSwitchInline":
        same_peer = bool(getattr(fields, "same_peer", False))
        item["type"] = "switch_inline_same" if same_peer else "switch_inline"
        if text:
            item["text"] = text
        item["data"] = getattr(fields, "query", "") or ""
    elif cls == "KeyboardButtonGame":
        item["type"] = "game"
        if text:
            item["text"] = text
    elif cls == "KeyboardButtonBuy":
        item["type"] = "buy"
        if text:
            item["text"] = text
    elif cls == "KeyboardButtonUserProfile":
        item["type"] = "user_profile"
        if text:
            item["text"] = text
        user_id = getattr(fields, "user_id", None)
        item["data"] = str(int(user_id)) if user_id else ""
    elif cls == "KeyboardButtonCopy":
        item["type"] = "copy_text"
        if text:
            item["text"] = text
        item["data"] = getattr(fields, "copy_text", "") or ""
    elif cls in ("KeyboardButtonRequestPhone", "KeyboardButtonRequestLocation",
                 "KeyboardButtonRequestPoll", "KeyboardButtonRequestPeer"):
        item["type"] = {
            "KeyboardButtonRequestPhone": "request_phone",
            "KeyboardButtonRequestLocation": "request_location",
            "KeyboardButtonRequestPoll": "request_poll",
            "KeyboardButtonRequestPeer": "request_peer",
        }[cls]
        if text:
            item["text"] = text
    else:
        # Unknown/unsupported constructor: do not crash, keep what we can.
        item["type"] = "disabled"
        if text:
            item["text"] = text
    return item


def serialize_reply_markup(message, *, chat_id=None, message_id=None,
                           failures=None):
    """Return inline_bot_buttons value or None.

    Only ReplyInlineMarkup (inline keyboards attached to messages) is
    exported; reply-keyboard markups cannot appear on server history
    messages and tdesktop does not export them either.
    """
    markup = getattr(message, "reply_markup", None)
    if markup is None:
        return None
    cls = type(markup).__name__
    if cls != "ReplyInlineMarkup":
        return None
    rows_out = []
    for row_index, row in enumerate(getattr(markup, "rows", []) or []):
        buttons = getattr(row, "buttons", []) or []
        row_out = [
            serialize_button(
                button, chat_id=chat_id, message_id=message_id,
                row=row_index, column=col, failures=failures)
            for col, button in enumerate(buttons)
        ]
        if row_out:
            rows_out.append(row_out)
    return rows_out or None


# --------------------------------------------------------------------------
# Dates / ids / names
# --------------------------------------------------------------------------

def format_date(epoch_seconds: int) -> str:
    """tdesktop SerializeDate: local-time ISO 8601, seconds precision."""
    import datetime
    dt = datetime.datetime.fromtimestamp(epoch_seconds).replace(microsecond=0)
    return dt.isoformat()


def format_peer_id(peer) -> str:
    """user<id> / chat<id> / channel<id>, like tdesktop's wrapPeerId."""
    cls = type(peer).__name__
    peer_id = getattr(peer, "id", None)
    if cls == "User":
        return f"user{int(peer_id)}"
    if cls == "Chat":
        return f"chat{int(peer_id)}"
    if cls == "Channel":
        return f"channel{int(peer_id)}"
    return f"user{int(peer_id) if peer_id else 0}"


def peer_name(peer) -> str:
    """tdesktop Peer::name equivalent (empty when unknown)."""
    if peer is None:
        return ""
    title = getattr(peer, "title", None)
    if title:
        return title
    first = getattr(peer, "first_name", None) or ""
    last = getattr(peer, "last_name", None) or ""
    if first and last:
        return f"{first} {last}"
    return first or last or getattr(peer, "username", "") or ""

# Schema mapping report — Telegram Desktop `result.json` ↔ MTProto/Telethon

Ground truth used during implementation:
1. Sanitized observations from Telegram Desktop machine-readable JSON exports. The private reference exports are not included in this repository.
2. Telegram Desktop's export writer conventions and the schema mapping notes recorded during implementation.

## Verified conventions

| Convention | Rule |
|---|---|
| File layout | One chat → bare chat object. Multiple chats → `{"about": "...", "chats": {"about": "...", "list": [...]}}` |
| Indentation | One space per nesting level, no trailing whitespace |
| Key order | Insertion order used by Telegram Desktop's serializer |
| Strings | JSON escaping for quotes, backslashes, line breaks, tabs; Desktop's writer also escapes control characters and U+2028/U+2029 |
| Empty values | Empty values are generally skipped; literal `data: ""` is emitted for non-empty callback payloads |
| Dates | `date` is local-time ISO 8601 to seconds; `date_unixtime` is the UTC epoch as a string |
| User IDs | `user<id>`; chat IDs `chat<id>`; channels/supergroups `channel<id>` |
| Callback buttons | For non-empty payloads: URL-safe Base64 alphabet, trailing `=` omitted, and `data: ""`; empty payloads omit both fields. Applies to callback and callback-with-password |
| URL buttons | `data` contains the URL |
| Switch-inline buttons | `data` contains the raw query string |
| Auth buttons | `url`, optional `forward_text`, `button_id` |
| Text splitting | UTF-16 entity offsets; sequential entities, plain gaps; Desktop 7.2.9 can emit trailing empty plain parts for non-ASCII text |
| `text` and `text_entities` | One plain part serializes as a string; mixed parts as an array. `text_entities` is always an array of typed objects |
| Entity extras | text link `href`; pre `language`; mention name `user_id`; custom emoji `document_id`; collapsed blockquote emits `collapsed` only when true |
| Missing media | Desktop's placeholder strings are reproduced for non-downloaded, unavailable, or too-large files |
| Message key order | id, type, date, edited, sender/actor, forwarding/reply fields, media, text, buttons, reactions |

## Root object

| Telegram Desktop field | Source | Fidelity |
|---|---|---|
| `name` | Dialog/entity title | Resolved title as seen by the account |
| `type` | User/bot/self, basic group, channel/supergroup and public/private flags | Exact for supported peer types |
| `id` | Bare peer ID | Numeric root ID |
| `messages` | Full `iter_messages` history, reordered oldest to newest | Exact for accessible history |

Chat types: `bot_chat`, `saved_messages`, `personal_chat`, `private_group`, `public_supergroup`, `private_supergroup`, `public_channel`, `private_channel`.

## Message object

| Telegram Desktop field | Source | Notes |
|---|---|---|
| `id`, `type` | Message ID and constructor | `message`, `service`, or `unsupported` |
| `date`, `date_unixtime` | Message timestamp | Local ISO date and UTC epoch string |
| `edited`, `edited_unixtime` | `edit_date` | Omitted if absent |
| `from`, `from_id` | Resolved sender | `user<id>`, `chat<id>`, or `channel<id>` |
| `actor`, `actor_id` | Service-message sender | Used on service messages |
| `author` | `post_author` | Channel signature |
| `forwarded_from`, `forwarded_from_id` | Forward header | Hidden sender includes name only; IDs are never fabricated |
| `saved_from` | `fwd_from.saved_from_peer` | Resolved peer name |
| `reply_to_message_id`, `reply_to_peer_id` | Reply header | Available single-reply identifiers |
| `via_bot` | `via_bot_id` | Username with `@` prefix |
| `text`, `text_entities` | Message text and entities | Entity offsets sliced in UTF-16 units with Desktop 7.2.9 compatibility behavior |
| `inline_bot_buttons` | `ReplyInlineMarkup` rows | Row and button order preserved; callback bytes encoded without modification |
| Photo fields | Largest `PhotoSize` | Path/placeholder, file size, dimensions, TTL |
| Document fields | Document and attributes | File metadata, media type, MIME, dimensions, duration, sticker/audio metadata, TTL |
| Location/contact/venue | `MessageMediaGeo*`, contact, venue | Coordinates, live period, names, phone, place and address |
| Game/invoice | Game and invoice media | Available title, description, payment metadata |
| `poll` | Poll and result objects | Question, closed, voter totals, answers and chosen state |
| `reactions` | Message reaction results | Counts and emoji/custom emoji IDs |
| Service `action` | `MessageAction*` TL objects | Known actions mapped; unknown actions degrade safely |

## Inline button mapping

| TL constructor | Exported type | Exported payload |
|---|---|---|
| `KeyboardButtonCallback` or `KeyboardInlineButton(InlineButtonTypeCallback)` | `callback` / `callback_with_password` | Raw bytes → URL-safe Base64 without padding, plus empty `data` for non-empty bytes |
| `KeyboardButtonUrl` or `InlineButtonTypeUrl` | `url` | URL in `data` |
| `KeyboardButtonUrlAuth` or `InlineButtonTypeUrlAuth` | `auth` | `url`, optional `forward_text`, `button_id` |
| `KeyboardButtonWebView` or `InlineButtonTypeWebView` | `web_view` | URL in `data` |
| `KeyboardButtonSwitchInline` or `InlineButtonTypeSwitchInline` | `switch_inline` / `switch_inline_same` | Query in `data` |
| Game / buy button | `game` / `buy` | No payload |
| User profile | `user_profile` | User ID digits in `data` |
| Copy text | `copy_text` | Copy text in `data` |
| Request phone/location/poll/peer | Matching request type | Text only |
| Unknown constructor | `disabled` | Button text only; never crashes export |

Callback payloads remain opaque bytes. The serializer only Base64URL-encodes the raw TL byte string and validates decode(encode(payload)) equals the original. Telethon 1.45+ wraps inline action TL objects in `KeyboardInlineButton`; older constructors are also supported.

## Media folders and downloads

Optional downloads use chat-relative paths under `photos/`, `video_files/`, `animations/`, `stickers/`, `voice_messages/`, `round_video_messages/`, `audio_files/`, and `files/`. Names are collision-free and sanitized. Failed or omitted files use Desktop's placeholder text.

## Known limitations

- Reaction `recent` actor lists are not exposed through fetched history and are omitted.
- Album splitting, stories, and dice media follow Telegram Desktop's unsupported/TODO behavior.
- Some sender names depend on Telethon's entity cache; unknown peers are not fabricated.
- Fields not exposed to the account are omitted.
- Date display intentionally uses the exporting machine's local timezone.
- This repository snapshot has no `.git` directory, so a Git status/diff cannot be inspected from this checkout.

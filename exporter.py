#!/usr/bin/env python3
"""
Export Telegram conversations (user account, MTProto via Telethon) into
Telegram Desktop's machine-readable result.json format.

Passive export only: reads history, never clicks buttons, never sends
anything. Callback payload bytes are preserved exactly (see serializer.py).
"""

import json
import os
import random
import re

from telethon import TelegramClient, errors, types
from telethon.tl.types import (
    Channel,
    Chat,
    GeoPointEmpty,
    MessageActionChannelCreate,
    MessageActionChatAddUser,
    MessageActionChatCreate,
    MessageActionChatDeletePhoto,
    MessageActionChatDeleteUser,
    MessageActionChatEditPhoto,
    MessageActionChatEditTitle,
    MessageActionChatJoinedByLink,
    MessageActionChatJoinedByRequest,
    MessageActionChatMigrateTo,
    MessageActionContactSignUp,
    MessageActionGameScore,
    MessageActionGeoProximityReached,
    MessageActionGroupCall,
    MessageActionGroupCallScheduled,
    MessageActionHistoryClear,
    MessageActionInviteToGroupCall,
    MessageActionPhoneCall,
    MessageActionPinMessage,
    MessageActionScreenshotTaken,
    MessageActionSetChatTheme,
    MessageActionSetChatWallPaper,
    MessageActionSetMessagesTTL,
    MessageActionTopicCreate,
    MessageActionTopicEdit,
    MessageMediaGeoLive,
    MessageService,
    User,
)

import serializer as ser

# --------------------------------------------------------------------------
# media folder mapping (tdesktop DocumentFolder / photo paths)
# --------------------------------------------------------------------------

MEDIA_FOLDERS = {
    "photo": "photos",
    "video_file": "video_files",
    "animation": "animations",
    "sticker": "stickers",
    "voice_message": "voice_messages",
    "video_message": "round_video_messages",
    "audio_file": "audio_files",
    "document": "files",
}


def is_valid_ext(ext: str) -> bool:
    return bool(re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext or ""))


class Exporter:
    def __init__(self, client: TelegramClient, me: User):
        self.client = client
        self.me = me
        self.callback_buttons_found = 0
        self.callback_payloads_preserved = 0
        self.callback_failures = []
        self._names = {}
        self._users = {}
        self._chats = {}
        self._media_counters = {}
        self._used_paths = set()

    # ------------------------------------------------------------------
    # entity/name cache
    # ------------------------------------------------------------------

    async def resolve_peer(self, peer):
        """Resolve and cache a user/chat/channel entity."""
        if peer is None:
            return None
        key = (type(peer).__name__, getattr(peer, "id", None))
        if key not in self._names:
            try:
                entity = await self.client.get_entity(peer)
            except (ValueError, errors.RPCError, OSError):
                entity = None
            if entity is None:
                self._names[key] = None
            else:
                self._names[key] = entity
                eid = getattr(entity, "id", None)
                if isinstance(entity, User):
                    self._users[eid] = entity
                elif isinstance(entity, (Chat, Channel)):
                    self._chats[eid] = entity
        return self._names[key]

    async def name_of(self, peer) -> str:
        entity = await self.resolve_peer(peer)
        return ser.peer_name(entity)

    async def name_of_id(self, peer_id) -> str:
        if peer_id is None:
            return ""
        return await self.name_of(peer_id)

    async def peer_id_of_id(self, peer_id) -> str:
        if peer_id is None:
            return ""
        try:
            entity = await self.resolve_peer(peer_id)
        except Exception:
            entity = None
        return ser.format_peer_id(entity) if entity else ""

    # ------------------------------------------------------------------
    # main entry
    # ------------------------------------------------------------------

    async def export_chat(self, dialog, out_dir: str, download_media: bool,
                          progress_every=500):
        """Export one dialog into out_dir/result.json (+ media folders)."""
        os.makedirs(out_dir, exist_ok=True)
        for sub in set(MEDIA_FOLDERS.values()) | {"profile_photos"}:
            os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
        self._out_dir = os.path.abspath(out_dir)

        self.callback_buttons_found = 0
        self.callback_payloads_preserved = 0
        self.callback_failures = []
        self._names = {}
        self._users = {}
        self._chats = {}
        self._media_counters = {}
        self._used_paths = set()

        peer = dialog.input_entity
        peer_id = dialog.id

        # MessageService and Message both derive from Message; fetch all.
        messages = await fetch_all_messages(
            self.client, peer, progress_every=progress_every)
        print(f"Messages fetched: {len(messages)}", flush=True)

        serialized = []
        for index, msg in enumerate(messages):
            try:
                serialized.append(await self.serialize_message(
                    msg, download_media, peer_id))
            except Exception as exc:  # noqa: BLE001 - keep export alive
                print(f"WARNING: failed to serialize message "
                      f"id={getattr(msg, 'id', '?')}: {exc!r}", flush=True)
                serialized.append({
                    "id": getattr(msg, "id", 0),
                    "type": "unsupported",
                })

        result = {
            "name": ser.peer_name(
                dialog.entity) or await self.name_of(dialog.input_entity),
            "type": self.chat_type_name(dialog),
            "id": peer_id,
            "messages": serialized,
        }

        out_path = os.path.join(out_dir, "result.json")
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1,
                      separators=(",", ": "))
            handle.write("\n")

        if self.callback_failures:
            for failure in self.callback_failures:
                print(f"WARNING: Unable to preserve callback payload "
                      f"{failure}", flush=True)
        return out_path

    # ------------------------------------------------------------------
    # chat classification
    # ------------------------------------------------------------------

    @staticmethod
    def chat_type_name(dialog) -> str:
        entity = dialog.entity
        if isinstance(entity, User):
            if getattr(entity, "is_self", False):
                return ser.CHAT_TYPE_SAVED
            if getattr(entity, "bot", False):
                return ser.CHAT_TYPE_BOT
            return ser.CHAT_TYPE_PERSONAL
        if isinstance(entity, Chat):
            return ser.CHAT_TYPE_PRIVATE_GROUP
        if isinstance(entity, Channel):
            broadcast = bool(getattr(entity, "broadcast", False))
            megagroup = bool(getattr(entity, "megagroup", False))
            public = bool(getattr(entity, "username", None))
            if broadcast:
                return (ser.CHAT_TYPE_PUBLIC_CHANNEL if public
                        else ser.CHAT_TYPE_PRIVATE_CHANNEL)
            return (ser.CHAT_TYPE_PUBLIC_SUPERGROUP
                    if public else ser.CHAT_TYPE_PRIVATE_SUPERGROUP)
        return ser.CHAT_TYPE_PERSONAL

    # ------------------------------------------------------------------
    # message serialization
    # ------------------------------------------------------------------

    async def serialize_message(self, msg, download_media: bool,
                                chat_bare_id: int) -> dict:
        msg_id = getattr(msg, "id", 0)
        date = int(msg.date.timestamp()) if getattr(msg, "date", None) else 0

        if isinstance(msg, MessageService):
            item = await self.serialize_service(msg, msg_id, date,
                                                chat_bare_id)
        else:
            item = {
                "id": msg_id,
                "type": "message",
                "date": ser.format_date(date),
                "date_unixtime": str(date),
            }
            await self.fill_regular_message(msg, msg_id, item,
                                            download_media, chat_bare_id)

        if getattr(msg, "reactions", None):
            reactions = self.serialize_reactions(msg.reactions)
            if reactions:
                item["reactions"] = reactions
        return item

    async def fill_regular_message(self, msg, msg_id: int, item: dict,
                                   download_media: bool, chat_bare_id: int):
        if getattr(msg, "edit_date", None):
            edited = int(msg.edit_date.timestamp())
            item["edited"] = ser.format_date(edited)
            item["edited_unixtime"] = str(edited)

        # from / from_id (tdesktop order: name then id)
        if msg.post_author:
            item["author"] = msg.post_author
        if msg.fwd_from is not None:
            fwd = msg.fwd_from
            if fwd.from_id is not None:
                item["forwarded_from"] = await self.name_of_id(fwd.from_id)
                item["forwarded_from_id"] = await self.peer_id_of_id(
                    fwd.from_id)
            elif fwd.from_name:
                item["forwarded_from"] = fwd.from_name
            if fwd.saved_from_peer is not None:
                item["saved_from"] = await self.name_of_id(fwd.saved_from_peer)
        elif msg.from_id is not None:
            item["from"] = await self.name_of_id(msg.from_id)
            item["from_id"] = await self.peer_id_of_id(msg.from_id)

        if msg.reply_to is not None:
            reply_to_msg_id = getattr(msg.reply_to, "reply_to_msg_id", None)
            if reply_to_msg_id:
                item["reply_to_message_id"] = int(reply_to_msg_id)
            reply_peer = getattr(msg.reply_to, "reply_to_peer_id", None)
            if reply_peer:
                item["reply_to_peer_id"] = await self.peer_id_of_id(reply_peer)

        if msg.via_bot_id:
            try:
                via = await self.resolve_peer_by_uid(msg.via_bot_id)
                username = getattr(via, "username", None)
                if username:
                    item["via_bot"] = f"@{username}"
            except Exception:
                pass

        await self.serialize_media(msg, msg_id, item, download_media,
                                   chat_bare_id)

        # text parts (UTF-16 offsets honored inside)
        text = msg.message or ""
        parts = ser.split_text_parts(text, msg.entities or [])
        item["text"] = ser.serialize_text_field(parts)
        item["text_entities"] = ser.serialize_text_entities(parts)

        failures_before = len(self.callback_failures)
        buttons = ser.serialize_reply_markup(
            msg, chat_id=chat_bare_id, message_id=msg_id,
            failures=self.callback_failures)
        if buttons:
            callback_types = ("callback", "callback_with_password")
            found = sum(
                1 for row in buttons for button in row
                if button["type"] in callback_types)
            self.callback_buttons_found += found
            # Payloads, including empty byte strings, remain byte-exact.
            self.callback_payloads_preserved += max(
                0, found - (len(self.callback_failures) - failures_before))
            item["inline_bot_buttons"] = buttons

    async def resolve_peer_by_uid(self, uid: int):
        key = ("User", uid)
        if key not in self._names:
            try:
                self._names[key] = await self.client.get_entity(
                    types.PeerUser(uid))
            except (ValueError, errors.RPCError, OSError):
                self._names[key] = None
        return self._names[key]

    # ------------------------------------------------------------------
    # media
    # ------------------------------------------------------------------

    def reserved_name(self, folder: str, base: str, ext: str) -> str:
        """Collision-free name inside folder; returns folder/name."""
        counters = self._media_counters
        counters[base] = counters.get(base, 0) + 1
        index = counters[base]
        for attempt in range(index, 10000):
            name = f"{base}{attempt}{ext}"
            rel = f"{folder}/{name}"
            if rel not in self._used_paths:
                self._used_paths.add(rel)
                return rel
        # extremely unlikely fallback
        rel = f"{folder}/{base}{attempt}{ext}.{random.randint(1000,9999)}"
        self._used_paths.add(rel)
        return rel

    async def serialize_media(self, msg, msg_id: int, item: dict,
                              download_media: bool, chat_bare_id: int):
        media = msg.media
        if media is None:
            return
        cls = type(media).__name__

        if cls == "MessageMediaPhoto" and media.photo is not None:
            await self.serialize_photo(media, msg, msg_id, item,
                                       download_media)
        elif cls == "MessageMediaDocument" and media.document is not None:
            await self.serialize_document(media, msg, msg_id, item,
                                          download_media)
        elif cls in ("MessageMediaGeo", "MessageMediaGeoLive"):
            geo = media.geo
            if geo is None or isinstance(geo, GeoPointEmpty):
                item["location_information"] = None  # JSON null, like tdesktop
            else:
                # tdesktop prints doubles via QByteArray::number ('g', 6 digits)
                item["location_information"] = {
                    "latitude": format(float(geo.lat), ".6g"),
                    "longitude": format(float(geo.long), ".6g"),
                }
            if isinstance(media, MessageMediaGeoLive):
                item["live_location_period_seconds"] = int(media.period)
        elif cls == "MessageMediaContact":
            info = {
                "first_name": media.first_name or "",
                "last_name": media.last_name or "",
                "phone_number": media.phone_number or "",
            }
            item["contact_information"] = info
        elif cls == "MessageMediaVenue":
            item["place_name"] = media.title or ""
            item["address"] = media.address or ""
            if media.geo is not None and not isinstance(
                    media.geo, GeoPointEmpty):
                item["location_information"] = {
                    "latitude": format(float(media.geo.lat), ".6g"),
                    "longitude": format(float(media.geo.long), ".6g"),
                }
        elif cls == "MessageMediaGame":
            game = media.game
            item["game_title"] = game.title or ""
            item["game_description"] = game.description or ""
        elif cls == "MessageMediaInvoice":
            item["invoice_information"] = {
                "title": media.title or "",
                "description": media.description or "",
                "amount": str(int(media.total_amount or 0)),
                "currency": media.currency or "",
            }
        elif cls == "MessageMediaPoll":
            self.serialize_poll(media, item)
        elif cls in ("MessageMediaUnsupported", "MessageMediaDice",
                     "MessageMediaStory"):
            item["media_type"] = "unsupported"
        else:
            item["media_type"] = "unsupported"

    async def serialize_photo(self, media, msg, msg_id: int, item: dict,
                              download_media: bool):
        photo = media.photo
        sizes = [s for s in (photo.sizes or [])
                 if type(s).__name__ in ("PhotoSize", "PhotoSizeProgressive")]
        largest = None
        if sizes:
            largest = max(
                sizes,
                key=lambda s: (getattr(s, "w", 0) or 0)
                * (getattr(s, "h", 0) or 0))
        date = int(msg.date.timestamp()) if msg.date else 0

        rel = None
        if download_media and largest is not None:
            rel = self.reserved_name("photos", "photo", ".jpg")
            target = os.path.join(self._out_dir, rel.replace("/", os.sep))
            try:
                downloaded = await self.client.download_media(msg, file=target)
                if not downloaded or not os.path.isfile(target):
                    rel = None
            except (errors.RPCError, OSError) as exc:
                print(f"WARNING: photo download failed "
                      f"message_id={msg_id}: {exc!r}", flush=True)
                rel = None

        if rel:
            item["photo"] = rel
            size = getattr(largest, "size", None) or 0
            if getattr(largest, "sizes", None):  # progressive
                size = largest.sizes[-1]
            if size:
                item["photo_file_size"] = int(size)
        else:
            item["photo"] = ser.FILE_NOT_INCLUDED
            size = getattr(largest, "size", None) or (
                largest.sizes[-1] if getattr(largest, "sizes", None) else 0)
            if size:
                item["photo_file_size"] = int(size)
        if largest is not None and getattr(largest, "w", 0):
            item["width"] = int(largest.w)
            item["height"] = int(largest.h)
        ttl = getattr(media, "ttl_seconds", None)
        if ttl:
            item["self_destruct_period_seconds"] = int(ttl)

    async def serialize_document(self, media, msg, msg_id: int, item: dict,
                                 download_media: bool):
        doc = media.document
        date = int(msg.date.timestamp()) if msg.date else 0

        attrs = {type(a).__name__: a for a in (doc.attributes or [])}
        doc_attr = attrs.get("DocumentAttributeFilename")
        file_name = (doc_attr.file_name if doc_attr else "") or ""
        sticker = attrs.get("DocumentAttributeSticker")
        animated = "DocumentAttributeAnimated" in attrs
        video = "DocumentAttributeVideo" in attrs
        audio = "DocumentAttributeAudio" in attrs
        voice = bool(getattr(attrs.get("DocumentAttributeAudio"),
                             "voice", False))
        round_msg = bool(getattr(attrs.get("DocumentAttributeVideo"),
                                 "round_message", False))

        mime = doc.mimeType or ""
        is_sticker = sticker is not None
        if is_sticker:
            media_type = "sticker"
        elif round_msg:
            media_type = "video_message"
        elif voice:
            media_type = "voice_message"
        elif animated:
            media_type = "animation"
        elif video:
            media_type = "video_file"
        elif audio:
            media_type = "audio_file"
        else:
            media_type = "document"

        folder = MEDIA_FOLDERS.get(media_type, "files")
        thumb_rel = None
        file_rel = None

        if download_media:
            ext = os.path.splitext(file_name)[1] if file_name else None
            if not ext or not is_valid_ext(ext):
                ext = self.mime_default_ext(mime, media_type)
            base = self.document_base_name(media_type, file_name, ext)
            file_rel = self.reserved_name(folder, base, ext)
            target = os.path.join(self._out_dir, file_rel.replace("/", os.sep))
            try:
                downloaded = await self.client.download_media(msg, file=target)
                if not downloaded or not os.path.isfile(target):
                    file_rel = None
            except (errors.RPCError, OSError) as exc:
                print(f"WARNING: file download failed "
                      f"message_id={msg_id}: {exc!r}", flush=True)
                file_rel = None
            thumb = next((t for t in (doc.thumbs or [])
                          if type(t).__name__ == "PhotoSize"), None)
            if thumb is not None:
                thumb_rel = self.reserved_name(folder, "thumb", ".jpg")
                try:
                    thumb_target = os.path.join(
                        self._out_dir, thumb_rel.replace("/", os.sep))
                    downloaded = await self.client.download_file(
                        doc, file=thumb_target, thumb=-1)
                    # download_file returns None when it writes to a path.
                    if not os.path.isfile(thumb_target):
                        thumb_rel = None
                except (errors.RPCError, OSError) as exc:
                    print(f"WARNING: thumbnail download failed "
                          f"message_id={msg_id}: {exc!r}", flush=True)
                    thumb_rel = None

        item["file"] = file_rel or ser.FILE_NOT_INCLUDED
        if file_name:
            item["file_name"] = file_name
        item["file_size"] = int(doc.size or 0)
        if thumb_rel:
            item["thumbnail"] = thumb_rel
            thumb = next((t for t in (doc.thumbs or [])
                          if type(t).__name__ == "PhotoSize"), None)
            if thumb is not None:
                item["thumbnail_file_size"] = int(thumb.size or 0)
        elif not download_media:
            thumb = next((t for t in (doc.thumbs or [])
                          if type(t).__name__ == "PhotoSize"), None)
            if thumb is not None:
                item["thumbnail"] = ser.FILE_NOT_INCLUDED
                item["thumbnail_file_size"] = int(thumb.size or 0)

        if media_type == "sticker":
            item["media_type"] = "sticker"
            item["sticker_emoji"] = getattr(sticker, "alt", "") or ""
        else:
            item["media_type"] = media_type
        item["mime_type"] = mime
        video_attr = attrs.get("DocumentAttributeVideo")
        audio_attr = attrs.get("DocumentAttributeAudio")
        duration = getattr(video_attr, "duration", None)
        if duration is None:
            duration = getattr(audio_attr, "duration", None)
        if duration:
            item["duration_seconds"] = int(duration)
        if audio and not voice:
            performer = getattr(audio_attr, "performer", None)
            title = getattr(audio_attr, "title", None)
            if performer:
                item["performer"] = performer
            if title:
                item["title"] = title
        attr = None
        if video or animated or round_msg:
            attr = video_attr
        elif is_sticker:
            attr = attrs.get("DocumentAttributeImageSize")
        if attr is not None and getattr(attr, "w", 0):
            item["width"] = int(attr.w)
            item["height"] = int(attr.h)
        ttl = getattr(media, "ttl_seconds", None)
        if ttl:
            item["self_destruct_period_seconds"] = int(ttl)

    def mime_default_ext(self, mime: str, media_type: str) -> str:
        table = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
            "video/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "text/plain": ".txt",
        }
        if mime in table:
            return table[mime]
        if media_type == "voice_message":
            return ".ogg"
        if media_type == "video_file":
            return ".mov"
        return ".unknown"

    def document_base_name(self, media_type: str, file_name: str,
                           ext: str) -> str:
        if file_name:
            stem = os.path.splitext(file_name)[0]
            if stem and is_valid_ext(ext):
                stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
                stem = stem.strip(". ")[:120]
                if stem:
                    return stem
        prefix = {
            "voice_message": "audio",
            "video_file": "video",
            "sticker": "sticker",
            "animation": "animation",
            "video_message": "video",
            "audio_file": "audio",
        }.get(media_type, "file")
        return prefix

    # ------------------------------------------------------------------
    # polls / reactions
    # ------------------------------------------------------------------

    def serialize_poll(self, media, item: dict):
        poll = media.poll
        results = media.results
        votes_by_option = {}
        chosen = set()
        if results is not None:
            for single in (results.results or []):
                opt = bytes(single.option)
                votes_by_option[opt] = int(single.voters or 0)
                if getattr(single, "chosen", False):
                    chosen.add(opt)
        answers = []
        for answer in (poll.answers or []):
            opt = bytes(answer.option)
            answer_parts = ser.split_text_parts(
                answer.text or "", [])
            answers.append({
                "text": ser.serialize_text_field(answer_parts),
                "voters": str(votes_by_option.get(opt, 0)),
                "chosen": "true" if opt in chosen else "false",
            })
        question_parts = ser.split_text_parts(
            getattr(poll.question, "text", "") or "", [])
        item["poll"] = {
            "question": ser.serialize_text_field(question_parts),
            "closed": "true" if getattr(poll, "closed", False) else "false",
            "total_voters": str(int(getattr(results, "total_voters", 0) or 0)),
            "answers": answers,
        }

    def serialize_reactions(self, reactions) -> list:
        out = []
        counts = {}
        for reaction in (reactions.results or []):
            cls = type(reaction).__name__
            if cls == "ReactionCount":
                emo = reaction.reaction
                emo_cls = type(emo).__name__
                key = None
                if emo_cls == "ReactionEmoji":
                    key = ("emoji", emo.emoticon)
                elif emo_cls == "ReactionCustomEmoji":
                    key = ("custom", int(emo.document_id))
                if key is None:
                    continue
                entry = counts.setdefault(key, {
                    "count": 0,
                    "type": "emoji" if key[0] == "emoji" else "custom_emoji",
                    "emoji": getattr(emo, "emoticon", ""),
                    "document_id": str(int(getattr(emo, "document_id", 0))),
                })
                entry["count"] += int(reaction.count or 0)
        for (kind, _), entry in counts.items():
            item = {"type": entry["type"], "count": str(entry["count"])}
            if kind == "emoji":
                item["emoji"] = entry["emoji"]
            else:
                item["document_id"] = entry["document_id"]
            out.append(item)
        return out

    # ------------------------------------------------------------------
    # service messages
    # ------------------------------------------------------------------

    async def serialize_service(self, msg, msg_id: int, date: int,
                                chat_bare_id: int) -> dict:
        item = {
            "id": msg_id,
            "type": "service",
            "date": ser.format_date(date),
            "date_unixtime": str(date),
        }
        action = msg.action
        cls = type(action).__name__

        if action is not None and msg.from_id is not None:
            item["actor"] = await self.name_of_id(msg.from_id)
            item["actor_id"] = await self.peer_id_of_id(msg.from_id)

        async def user_names(uids):
            names = []
            for uid in uids or []:
                names.append(await self.name_of_id(
                    types.PeerUser(int(uid))))
            return names

        if cls == "MessageActionChatCreate":
            item["action"] = "create_group"
            item["title"] = action.title or ""
            item["members"] = await user_names(action.users)
        elif cls == "MessageActionChannelCreate":
            item["action"] = "create_channel"
            item["title"] = action.title or ""
        elif cls == "MessageActionChatEditTitle":
            item["action"] = "edit_group_title"
            item["title"] = action.title or ""
        elif cls == "MessageActionChatEditPhoto":
            item["action"] = "edit_group_photo"
            await self.serialize_action_photo(action.photo, item)
        elif cls == "MessageActionChatDeletePhoto":
            item["action"] = "delete_group_photo"
        elif cls == "MessageActionChatAddUser":
            item["action"] = "invite_members"
            item["members"] = await user_names(action.users)
        elif cls == "MessageActionChatDeleteUser":
            item["action"] = "remove_members"
            item["members"] = await user_names([action.user_id])
        elif cls == "MessageActionChatJoinedByLink":
            item["action"] = "join_group_by_link"
            item["inviter"] = await self.name_of_id(
                types.PeerUser(int(action.inviter_id or 0)))
        elif cls == "MessageActionChatJoinedByRequest":
            item["action"] = "join_group_by_request"
        elif cls == "MessageActionChatMigrateTo":
            item["action"] = "migrate_to_supergroup"
        elif cls == "MessageActionPinMessage":
            item["action"] = "pin_message"
            if msg.reply_to is not None and getattr(
                    msg.reply_to, "reply_to_msg_id", None):
                item["message_id"] = int(msg.reply_to.reply_to_msg_id)
        elif cls == "MessageActionHistoryClear":
            item["action"] = "clear_history"
        elif cls == "MessageActionGameScore":
            item["action"] = "score_in_game"
            if msg.reply_to is not None and getattr(
                    msg.reply_to, "reply_to_msg_id", None):
                item["game_message_id"] = int(msg.reply_to.reply_to_msg_id)
            item["score"] = str(int(action.score or 0))
        elif cls == "MessageActionPhoneCall":
            item["action"] = "phone_call"
            if action.duration:
                item["duration_seconds"] = int(action.duration)
            reason = {
                "PhoneCallDiscardReasonBusy": "busy",
                "PhoneCallDiscardReasonDisconnect": "disconnect",
                "PhoneCallDiscardReasonHangup": "hangup",
                "PhoneCallDiscardReasonMissed": "missed",
            }.get(type(getattr(action, "reason", None)).__name__, "")
            if reason:
                item["discard_reason"] = reason
        elif cls == "MessageActionScreenshotTaken":
            item["action"] = "take_screenshot"
        elif cls == "MessageActionContactSignUp":
            item["action"] = "joined_telegram"
        elif cls == "MessageActionGeoProximityReached":
            item["action"] = "proximity_reached"
            if action.from_id is not None:
                item["from"] = await self.name_of_id(action.from_id)
                item["from_id"] = await self.peer_id_of_id(action.from_id)
            if action.to_id is not None:
                item["to"] = await self.name_of_id(action.to_id)
                item["to_id"] = await self.peer_id_of_id(action.to_id)
            item["distance"] = str(int(action.distance or 0))
        elif cls == "MessageActionGroupCall":
            item["action"] = "group_call"
            if action.duration:
                item["duration"] = str(int(action.duration))
        elif cls == "MessageActionGroupCallScheduled":
            item["action"] = "group_call_scheduled"
            item["schedule_date"] = str(int(action.schedule_date or 0))
        elif cls == "MessageActionInviteToGroupCall":
            item["action"] = "invite_to_group_call"
            item["members"] = await user_names(action.users)
        elif cls == "MessageActionSetMessagesTTL":
            item["action"] = "set_messages_ttl"
            item["period"] = str(int(action.period or 0))
        elif cls == "MessageActionSetChatTheme":
            item["action"] = "edit_chat_theme"
            if action.emoticon:
                item["emoticon"] = action.emoticon
        elif cls == "MessageActionTopicCreate":
            item["action"] = "topic_created"
            item["title"] = action.title or ""
        elif cls == "MessageActionTopicEdit":
            item["action"] = "topic_edit"
            if action.title:
                item["new_title"] = action.title
        elif cls == "MessageActionSetChatWallPaper":
            item["action"] = "set_chat_wallpaper"
            if msg.reply_to is not None and getattr(
                    msg.reply_to, "reply_to_msg_id", None):
                item["message_id"] = int(msg.reply_to.reply_to_msg_id)
        else:
            # Unknown service action: keep the message, mark type safely.
            item["action"] = "unknown"
        return item

    async def serialize_action_photo(self, photo, item: dict):
        if photo is None:
            return
        sizes = [s for s in (photo.sizes or [])
                 if type(s).__name__ in ("PhotoSize",
                                         "PhotoSizeProgressive")]
        if not sizes:
            return
        largest = max(sizes, key=lambda s: (getattr(s, "w", 0) or 0)
                      * (getattr(s, "h", 0) or 0))
        item["photo"] = ser.FILE_NOT_INCLUDED
        size = getattr(largest, "size", None) or (
            largest.sizes[-1] if getattr(largest, "sizes", None) else 0)
        if size:
            item["photo_file_size"] = int(size)
        if getattr(largest, "w", 0):
            item["width"] = int(largest.w)
            item["height"] = int(largest.h)


# --------------------------------------------------------------------------
# dialogs listing / selection
# --------------------------------------------------------------------------

async def list_dialogs(client: TelegramClient):
    return await client.get_dialogs(limit=None)


async def fetch_all_messages(client, peer, progress_every=500):
    """Fetch every history page and return messages oldest to newest."""
    messages = []
    async for msg in client.iter_messages(peer, limit=None, reverse=False):
        messages.append(msg)
        if progress_every and len(messages) % progress_every == 0:
            print(f"Messages fetched: {len(messages)}", flush=True)
    messages.reverse()
    return messages


def display_dialog_line(index: int, dialog) -> str:
    name = dialog.name or "(unnamed)"
    entity = dialog.entity
    if isinstance(entity, User):
        kind = "Bot" if getattr(entity, "bot", False) else "Private"
        if getattr(entity, "is_self", False):
            kind = "Personal"
    elif isinstance(entity, Chat):
        kind = "Group"
    elif isinstance(entity, Channel):
        kind = ("Channel" if getattr(entity, "broadcast", False)
                else "Supergroup")
    else:
        kind = "Unknown"
    return f"[{index:>3}] {name:<40.40} {kind}"


def sanitize_dir_name(name: str) -> str:
    name = (name or "chat").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.rstrip(". ")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "chat"
    return name[:100]

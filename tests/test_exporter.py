import asyncio
import base64
import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import exporter
import serializer
from main import get_windows_system_proxy, parse_telethon_proxy


class ProxyConfigTests(unittest.TestCase):
    def test_unset_proxy_keeps_direct_connection(self):
        self.assertIsNone(parse_telethon_proxy(None))
        self.assertIsNone(parse_telethon_proxy(""))

    def test_proxy_url_maps_to_telethon_arguments(self):
        self.assertEqual(
            {
                "proxy_type": "socks5",
                "addr": "proxy.example",
                "port": 1080,
                "rdns": True,
                "username": "user",
                "password": "p@ss",
            },
            parse_telethon_proxy("socks5://user:p%40ss@proxy.example:1080"),
        )

    def test_invalid_proxy_url_does_not_echo_input(self):
        secret_url = "socks5://user:secret@proxy.example:notaport"
        with self.assertRaises(ValueError) as raised:
            parse_telethon_proxy(secret_url)
        self.assertNotIn("secret", str(raised.exception))

    def test_bracketed_ipv6_windows_proxy_is_supported(self):
        registry = SimpleNamespace(
            HKEY_CURRENT_USER=object(),
        )

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        registry.OpenKey = lambda *_args: FakeKey()
        registry.QueryValueEx = lambda _key, name: {
            "ProxyEnable": (1, 4),
            "ProxyServer": ("http=[::1]:8080", 1),
        }[name]

        with patch("main.sys.platform", "win32"), patch.dict(
                "sys.modules", {"winreg": registry}):
            proxy = get_windows_system_proxy()
        self.assertEqual("::1", proxy["addr"])
        self.assertEqual(8080, proxy["port"])
        self.assertEqual("http", proxy["proxy_type"])


class Entity:
    def __init__(self, offset, length, **kwargs):
        self.offset = offset
        self.length = length
        for key, value in kwargs.items():
            setattr(self, key, value)


class MessageEntityBold(Entity):
    pass


class MessageEntityBlockquote(Entity):
    pass


class ReplyInlineMarkup:
    def __init__(self, rows):
        self.rows = rows


class KeyboardButtonCallback:
    def __init__(self, text, data, requires_password=False):
        self.text = text
        self.data = data
        self.requires_password = requires_password


class KeyboardButtonUrl:
    def __init__(self, text, url):
        self.text = text
        self.url = url


class CallbackTests(unittest.TestCase):
    def test_base64url_alphabet_padding_and_byte_integrity(self):
        vectors = {
            b"\xff": "_w",       # one byte: two padding chars omitted
            b"\xfb\xff": "-_8", # distinguishes both URL alphabet chars
            b"\xfb\xff\xff": "-___",  # two bytes: one padding char omitted
            b"abc": "YWJj",      # no padding needed
        }
        for raw, expected in vectors.items():
            with self.subTest(raw=raw):
                encoded = serializer.encode_callback_data(raw)
                self.assertEqual(expected, encoded)
                self.assertNotIn("=", encoded)
                self.assertEqual(raw, serializer.decode_callback_data(encoded))
        self.assertEqual("+/8=", base64.b64encode(b"\xfb\xff").decode())

    def test_reference_data_base64_samples(self):
        # Samples copied from the supplied Telegram Desktop result.json files.
        samples = {
            "YW5zd2VyLTUzNzg1MTIyMTg": b"answer-5378512218",
            "YmxvY2stNTM3ODUxMjIxOA": b"block-5378512218",
            "NzAwMTc3MTczOC8xNzg5Njk0NjI5L3JlcG9ydA":
                b"7001771738/1789694629/report",
            "NzAwMTc3MTczOC8xNzg5Njk0NjI5L2FuczE":
                b"7001771738/1789694629/ans1",
        }
        for encoded, raw in samples.items():
            with self.subTest(encoded=encoded):
                self.assertEqual(raw, serializer.decode_callback_data(encoded))
                self.assertEqual(encoded, serializer.encode_callback_data(raw))

    def test_empty_payload_matches_desktop_omission(self):
        button = KeyboardButtonCallback("Empty", b"")
        self.assertEqual(
            {"type": "callback", "text": "Empty"},
            serializer.serialize_button(button))

    def test_raw_telethon_145_callback_bytes(self):
        from telethon.tl.types import (
            InlineButtonTypeCallback, KeyboardInlineButton,
            KeyboardInlineButtonRow, ReplyInlineMarkup,
        )

        raw = b"\x00\xfb\xff\x80"
        button = KeyboardInlineButton(
            text="Raw", type=InlineButtonTypeCallback(data=raw))
        item = serializer.serialize_button(button)
        self.assertEqual("callback", item["type"])
        self.assertEqual(raw, serializer.decode_callback_data(item["dataBase64"]))
        message = SimpleNamespace(reply_markup=ReplyInlineMarkup(rows=[
            KeyboardInlineButtonRow(buttons=[button]),
        ]))
        rows = serializer.serialize_reply_markup(message)
        self.assertEqual(raw, serializer.decode_callback_data(
            rows[0][0]["dataBase64"]))

    def test_callback_password_flag_preserves_payload(self):
        raw = b"\x00\xff\x80\xfb\xff"
        item = serializer.serialize_button(
            KeyboardButtonCallback("Confirm", raw, True))
        self.assertEqual("callback_with_password", item["type"])
        self.assertEqual(raw, serializer.decode_callback_data(item["dataBase64"]))
        self.assertEqual("", item["data"])


class KeyboardTests(unittest.TestCase):
    def test_rows_and_column_order_are_preserved(self):
        markup = ReplyInlineMarkup([
            SimpleNamespace(buttons=[
                KeyboardButtonUrl("First", "https://one"),
                KeyboardButtonCallback("Second", b"2"),
            ]),
            SimpleNamespace(buttons=[
                KeyboardButtonCallback("Third", b"3"),
            ]),
        ])
        msg = SimpleNamespace(reply_markup=markup)
        result = serializer.serialize_reply_markup(msg)
        self.assertEqual(2, len(result))
        self.assertEqual(["First", "Second"], [b["text"] for b in result[0]])
        self.assertEqual("Third", result[1][0]["text"])
        self.assertEqual("Mg", result[0][1]["dataBase64"])


class TextAndOutputTests(unittest.TestCase):
    def test_utf16_entity_offsets_cover_emoji(self):
        parts = serializer.split_text_parts(
            "A😀سلام", [MessageEntityBold(1, 2)])
        self.assertEqual(
            [{"type": "plain", "text": "A"},
             {"type": "bold", "text": "😀"},
             {"type": "plain", "text": "سلام"}],
            parts)

    def test_tdesktop_persian_trailing_empty_compatibility(self):
        parts = serializer.split_text_parts("سلام", [MessageEntityBold(0, 4)])
        self.assertEqual("سلام", parts[0]["text"])
        self.assertEqual({"type": "plain", "text": ""}, parts[-1])
        self.assertEqual(
            [{"type": "bold", "text": "سلام"}, ""],
            serializer.serialize_text_field(parts))

    def test_blockquote_collapsed_flag_is_kept_only_when_true(self):
        parts = serializer.split_text_parts(
            "quote", [MessageEntityBlockquote(0, 5, collapsed=True)])
        self.assertTrue(parts[0]["collapsed"])

    def test_json_validity(self):
        value = {"text": "سلام 😀", "dataBase64": "-_8"}
        self.assertEqual(value, json.loads(json.dumps(value, ensure_ascii=False)))

    def test_local_time_date_serialization(self):
        stamp = 1_700_000_000
        expected = datetime.datetime.fromtimestamp(stamp).replace(
            microsecond=0).isoformat()
        self.assertEqual(expected, serializer.format_date(stamp))


class HistoryTests(unittest.TestCase):
    def test_full_paginated_history_is_chronological(self):
        class FakeClient:
            async def iter_messages(self, peer, limit=None, reverse=False):
                self.args = (peer, limit, reverse)
                # Fake a paginated server iterator, newest first.
                for page in (range(999, 498, -1), range(498, -1, -1)):
                    for message_id in page:
                        yield SimpleNamespace(id=message_id)

        client = FakeClient()
        result = asyncio.run(exporter.fetch_all_messages(
            client, "peer", progress_every=0))
        self.assertEqual(("peer", None, False), client.args)
        self.assertEqual(1000, len(result))
        self.assertEqual(list(range(1000)), [m.id for m in result])


if __name__ == "__main__":
    unittest.main()

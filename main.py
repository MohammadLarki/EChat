#!/usr/bin/env python3
"""
Telegram JSON Exporter — export your own Telegram user account's chats into
Telegram Desktop's machine-readable result.json format.

Runs on Android/Termux with Python 3 and Telethon (MTProto user API).
Passive export only: never clicks buttons, never sends messages.

    python main.py
"""

import asyncio
import getpass
import json
import os
import shutil
import sys
from typing import Optional
from urllib.parse import unquote, urlsplit

from telethon import TelegramClient, errors

import exporter as ex
import serializer as ser
import cli_display

SESSION_NAME = "tg_export_session"
API_ID_FILE = "api_credentials.json"
def default_export_root():
    """Use Android shared Downloads on Termux when available, else local exports."""
    if os.environ.get("TERMUX_VERSION") or "com.termux" in os.environ.get("PREFIX", ""):
        shared = os.path.expanduser("~/storage/shared/Download")
        if os.path.isdir(shared) and os.access(shared, os.W_OK):
            return os.path.join(shared, "EChat")
    return "exports"


EXPORT_ROOT = default_export_root()
PROXY_ENV = "TELETHON_PROXY"

MENU = """
Telegram JSON Exporter

1. Export one chat
2. Export multiple chats
3. Export all chats
4. Exit
"""


# ----------------------------------------------------------------------
# authentication
# ----------------------------------------------------------------------

def load_saved_credentials():
    """Load api_id/api_hash saved locally (never uploaded anywhere)."""
    if not os.path.exists(API_ID_FILE):
        return None
    try:
        with open(API_ID_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        api_id = int(data.get("api_id") or 0)
        api_hash = data.get("api_hash") or ""
        if api_id > 0 and api_hash:
            return api_id, api_hash
    except (OSError, ValueError):
        pass
    return None


def ask_credentials():
    print("First run: Telegram API credentials are required.")
    print("Get them at https://my.telegram.org -> API development tools.")
    while True:
        try:
            api_id = int(getpass.getpass("api_id (hidden): ").strip())
            api_hash = getpass.getpass("api_hash (hidden): ").strip()
            if api_id > 0 and api_hash:
                return api_id, api_hash
        except ValueError:
            pass
        print("Invalid input, try again.")


def save_credentials(api_id: int, api_hash: str):
    with open(API_ID_FILE, "w", encoding="utf-8") as handle:
        json.dump({"api_id": api_id, "api_hash": api_hash}, handle)
    if os.name == "posix":
        os.chmod(API_ID_FILE, 0o600)


def parse_telethon_proxy(value: Optional[str]):
    """Parse a proxy URL into Telethon's documented dict format."""
    if not value:
        return None

    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError(
            f"{PROXY_ENV} must be a valid socks5://, socks4://, or http:// URL."
        ) from None

    if (scheme not in {"socks5", "socks4", "http"} or not host or not port
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError(
            f"{PROXY_ENV} must be a valid socks5://, socks4://, or http:// URL."
        )

    username = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else None
    proxy = {
        "proxy_type": scheme,
        "addr": host,
        "port": port,
        "rdns": True,
    }
    if username is not None:
        proxy["username"] = username
    if password is not None:
        proxy["password"] = password
    return proxy


def get_windows_system_proxy():
    """Read a manually configured Windows proxy without exposing credentials."""
    if sys.platform != "win32":
        return None

    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except (OSError, ValueError):
        return None

    if not enabled or not isinstance(proxy_server, str) or not proxy_server.strip():
        return None

    # Windows accepts either one endpoint (commonly host:port) or a list such
    # as "http=host:port;https=host:port;socks=host:port". The ProxyServer
    # value is a manual HTTP proxy setting unless a scheme is explicitly given.
    entries = {}
    for item in proxy_server.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            kind, endpoint = item.split("=", 1)
            entries[kind.strip().lower()] = endpoint.strip()
        else:
            entries["http"] = item

    selected_kind = next((kind for kind in ("https", "http", "socks")
                          if entries.get(kind)), None)
    if selected_kind is None and entries:
        selected_kind = next(iter(entries))
    endpoint = entries.get(selected_kind) if selected_kind else None
    if not endpoint:
        return None

    # An explicitly URL-shaped value also lets users configure SOCKS/HTTP
    # proxies in Windows Internet Settings.
    if "://" in endpoint:
        return parse_telethon_proxy(endpoint)

    try:
        if endpoint.startswith("["):
            parsed = urlsplit(f"//{endpoint}")
            host, port = parsed.hostname, parsed.port
        else:
            host, port_text = endpoint.rsplit(":", 1)
            port = int(port_text)
    except (ValueError, TypeError):
        raise ValueError(
            "Windows has a manual proxy setting Telethon cannot parse. "
            "Set TELETHON_PROXY to an explicit proxy URL."
        ) from None
    if not host or not port or not 1 <= port <= 65535:
        raise ValueError(
            "Windows has a manual proxy setting Telethon cannot parse. "
            "Set TELETHON_PROXY to an explicit proxy URL."
        )

    proxy_type = "socks5" if selected_kind == "socks" else "http"
    return {"proxy_type": proxy_type, "addr": host, "port": port, "rdns": True}


async def ensure_client() -> TelegramClient:
    creds = load_saved_credentials()
    if creds:
        api_id, api_hash = creds
    else:
        api_id, api_hash = ask_credentials()
        save_credentials(api_id, api_hash)

    proxy_source = None
    try:
        configured_proxy = os.environ.get(PROXY_ENV)
        if configured_proxy:
            proxy = parse_telethon_proxy(configured_proxy)
            proxy_source = PROXY_ENV
        else:
            proxy = get_windows_system_proxy()
            if proxy:
                proxy_source = "Windows Internet Settings"
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1) from None

    if proxy:
        try:
            import python_socks  # noqa: F401 - Telethon uses it for async proxying.
        except ImportError:
            print(
                "Proxy support needs python-socks; "
                "run: pip install -r requirements.txt"
            )
            raise SystemExit(1) from None
        print(f"Using proxy from {proxy_source}.")

    client = TelegramClient(SESSION_NAME, api_id, api_hash,
                            device_model="Desktop",
                            system_version="Windows 10",
                            app_version="7.2.9 x64",
                            proxy=proxy)
    try:
        await client.connect()
    except (OSError, asyncio.TimeoutError):
        if proxy:
            print(
                f"Could not connect to Telegram through {proxy_source}. "
                "Check the proxy type, address, port, and that it can reach Telegram."
            )
        else:
            print("Could not connect directly to Telegram. This network may block Telegram; configure TELETHON_PROXY and retry.")
        await client.disconnect()
        raise SystemExit(1) from None
    if not await client.is_user_authorized():
        print("Login required.")
        phone = getpass.getpass("Phone number (international format, hidden): ").strip()
        try:
            await client.send_code_request(phone)
        except (errors.RPCError, OSError):
            print("Could not send login code. Check the network and account number.")
            raise SystemExit(1)
        # Keep all authentication input out of terminal echo and logs.
        code = getpass.getpass("Login code (hidden): ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except errors.SessionPasswordNeededError:
            password = getpass.getpass("2FA password: ")
            try:
                await client.sign_in(password=password)
            except (errors.RPCError, OSError):
                print("Login failed. Check the two-step verification password.")
                raise SystemExit(1) from None
        except (errors.RPCError, OSError):
            print("Login failed. Check the code and account password.")
            raise SystemExit(1)
    me = await client.get_me()
    print(f"Logged in.")
    return client


# ----------------------------------------------------------------------
# chat selection helpers
# ----------------------------------------------------------------------

def print_dialogs(dialogs, start=1):
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    for line in cli_display.dialog_lines(dialogs, start, width):
        print(line)


def choose_dialogs(dialogs, prompt="Select a chat:"):
    """Interactive selection; returns list of dialogs."""
    print(prompt)
    print_dialogs(dialogs, 1)
    while True:
        raw = input("\n> ").strip()
        if not raw:
            print("Please enter a number.")
            continue
        try:
            index = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= index <= len(dialogs):
            return [dialogs[index - 1]]
        print(f"Number out of range (1-{len(dialogs)}).")


def paginate(dialogs):
    """Simple pagination for accounts with many dialogs."""
    page_size = 30
    offset = 0
    while True:
        page = dialogs[offset:offset + page_size]
        print(f"\nShowing {offset + 1}-{offset + len(page)} "
              f"of {len(dialogs)}\n")
        print_dialogs(page, offset + 1)
        raw = input(
            "[n]ext / [p]rev / number / [s]earch / [q]uit > ").strip().lower()
        if raw in ("n", ""):
            if offset + page_size < len(dialogs):
                offset += page_size
        elif raw == "p" and offset > 0:
            offset -= page_size
        elif raw == "q":
            return None
        elif raw == "s":
            needle = input("Search name: ").strip().lower()
            matches = [d for d in dialogs
                       if needle in (d.name or "").lower()]
            print(f"{len(matches)} match(es):")
            for index, dialog in enumerate(matches, 1):
                print(ex.display_dialog_line(index, dialog))
            raw2 = input("Pick a number (empty to cancel) > ").strip()
            try:
                idx = int(raw2)
                if 1 <= idx <= len(matches):
                    return [matches[idx - 1]]
            except ValueError:
                pass
        else:
            try:
                idx = int(raw)
                if 1 <= idx <= len(dialogs):
                    return [dialogs[idx - 1]]
                print(f"Number out of range (1-{len(dialogs)}).")
            except ValueError:
                print("Please enter a number, n, p, s or q.")


def pick_one(dialogs):
    if len(dialogs) <= 20:
        return choose_dialogs(dialogs, "Select a chat:")
    return paginate(dialogs)


def pick_many(dialogs):
    print("Select a chat:")
    print_dialogs(dialogs, 1)
    raw = input("\nEnter chat numbers separated by spaces "
                "(e.g. 3 17 42) > ").strip()
    chosen = []
    for token in raw.replace(",", " ").split():
        try:
            idx = int(token)
        except ValueError:
            continue
        if 1 <= idx <= len(dialogs) and dialogs[idx - 1] not in chosen:
            chosen.append(dialogs[idx - 1])
    return chosen


def ask_download_media() -> bool:
    answer = input("Download media? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


# ----------------------------------------------------------------------
# export orchestration
# ----------------------------------------------------------------------

def unique_dir(base_root: str, name: str) -> str:
    candidate = ex.sanitize_dir_name(name)
    path = os.path.join(base_root, candidate)
    counter = 2
    while os.path.exists(path) and counter < 1000:
        path = os.path.join(base_root, f"{candidate} ({counter})")
        counter += 1
    return path


async def run_export(client, dialog, download_media: bool):
    name = dialog.name or "chat"
    os.makedirs(EXPORT_ROOT, exist_ok=True)
    print(f"\nExporting {name}...")
    out_dir = unique_dir(EXPORT_ROOT, name)
    exporter = ex.Exporter(client, await client.get_me())
    try:
        out_path = await exporter.export_chat(
            dialog, out_dir, download_media)
    except errors.FloodWaitError as exc:
        print(f"WARNING: FloodWait {exc.seconds}s while exporting "
              f"{name}; chat skipped.")
        return None
    except (errors.RPCError, OSError) as exc:
        print(f"WARNING: chat failed ({name}): {exc!r}")
        return None

    preserved = exporter.callback_payloads_preserved
    found = exporter.callback_buttons_found
    failures = len(exporter.callback_failures)
    print(f"\nCallback buttons found: {found}")
    print(f"Callback payloads preserved: {preserved}")
    print(f"Callback payload failures: {failures}")
    with open(out_path, "r", encoding="utf-8") as handle:
        message_count = len(json.load(handle)["messages"])
    print(f"\nMessages: {message_count}")
    print(f"\nOutput:\n{out_path}")
    return exporter


async def main_async():
    client = await ensure_client()
    try:
        while True:
            print(MENU)
            choice = input("Choose an option (1-4): ").strip()
            if choice == "1":
                dialogs = await ex.list_dialogs(client)
                if not dialogs:
                    print("No dialogs found.")
                    continue
                picked = pick_one(dialogs)
                if picked:
                    dl = ask_download_media()
                    await run_export(client, picked[0], dl)
            elif choice == "2":
                dialogs = await ex.list_dialogs(client)
                picked = pick_many(dialogs)
                if not picked:
                    print("Nothing selected.")
                    continue
                dl = ask_download_media()
                await export_many(client, picked, dl)
            elif choice == "3":
                dialogs = await ex.list_dialogs(client)
                dl = ask_download_media()
                await export_many(client, dialogs, dl)
            elif choice == "4":
                print("Bye.")
                return
            else:
                print("Invalid choice.")
    finally:
        await client.disconnect()


async def export_many(client, dialogs, download_media: bool):
    """Export several chats; one failure never stops the rest."""
    successful = failed = 0
    totals = {"found": 0, "preserved": 0, "failures": 0}
    for index, dialog in enumerate(dialogs, 1):
        print(f"\n=== Chat {index}/{len(dialogs)} ===")
        try:
            result = await run_export(client, dialog, download_media)
        except Exception as exc:  # noqa: BLE001 - isolation by design
            print(f"WARNING: chat failed ({dialog.name}): {exc!r}")
            result = None
        if result is None:
            failed += 1
        else:
            successful += 1
            totals["found"] += result.callback_buttons_found
            totals["preserved"] += result.callback_payloads_preserved
            totals["failures"] += len(result.callback_failures)
    print("\nExport complete.")
    print(f"Successful chats: {successful}")
    print(f"Failed chats: {failed}")
    print(f"\nCallback buttons found: {totals['found']}")
    print(f"Callback payloads preserved: {totals['preserved']}")
    print(f"Callback failures: {totals['failures']}")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()

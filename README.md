# EChat

EChat exports chats from your own Telegram account to Telegram Desktop's
machine-readable JSON format. It is a small Python command-line application
for Windows and Android through Termux. It uses Telegram's MTProto user API;
it does not use the Bot API and has no graphical interface.

The exporter reads chat history. It does not send messages, click inline
buttons, or invoke bot actions. Export only chats and data you are authorized
to access.

## Features

- Export one chat, selected chats, or all accessible chats.
- Read the complete history available to your account and write messages in
  oldest-to-newest order.
- Optionally download media into folders referenced by the JSON.
- Preserve message text entities, replies, forwards, service actions,
  reactions, polls, locations, contacts, and inline keyboards where Telegram
  exposes the relevant data.
- Keep callback button payloads byte-exact. For non-empty payloads, EChat
  writes URL-safe Base64 without `=` padding in `dataBase64`, alongside the
  empty `data` field used by Telegram Desktop. Decoding `dataBase64` returns
  the original callback bytes. Empty payloads omit both fields.
- Use a two-line chat picker on narrow terminals, including Termux screens.

## Telegram Desktop JSON compatibility

Each exported chat is written as a `result.json` chat object. Batch exports
create a Telegram Desktop-style root object containing the chat list. JSON
field names, message order, media paths, and callback button conventions are
chosen to match Telegram Desktop's **machine-readable JSON** export.

Compatibility is field-by-field, not a claim that every Telegram Desktop
version and every possible Telegram object will produce identical output.
The data available through the account's MTProto history, Telethon's entity
cache, and the selected download options limits what can be represented.
Unavailable values are omitted or represented with Desktop-like media
placeholders rather than invented. See [SCHEMA_MAPPING.md](SCHEMA_MAPPING.md)
for the current mapping and known gaps.

### Callback payload bytes

Callback payloads are opaque bytes. EChat does not decode, normalize, or
reinterpret them. It applies Telegram Desktop's URL-safe Base64 alphabet and
omits trailing padding. The serializer verifies the round trip from the
encoded value back to the raw bytes. For each export, the CLI reports the
number of callback buttons found, preserved payloads, and failures.

## Android and Termux setup

1. Install Termux from a trusted source, open it, and update packages:

   ```sh
   pkg update
   pkg upgrade
   pkg install python unzip
   ```

2. Download the complete `echat-release.zip` to the phone's Downloads folder.
   Grant Termux storage access when Android asks, then extract the project:

   ```sh
   termux-setup-storage
   mkdir -p ~/echat
   unzip ~/storage/downloads/echat-release.zip -d ~/echat
   cd ~/echat
   ```

3. Install the runtime dependencies and start EChat:

   ```sh
   python -m pip install -r requirements.txt
   python main.py
   ```

The chat picker puts each chat type and title on separate lines on narrow
screens. Termux and some terminal emulators do not display right-to-left
Persian/Arabic text correctly. EChat does not try to reshape fonts or reorder
letters: the terminal may show a title awkwardly, while the source text and
the JSON export stay unchanged.

## Windows setup

1. Install a supported Python 3 version from [python.org](https://www.python.org/downloads/)
   and enable the Python launcher (`py`).
2. Extract the complete ZIP to a folder you control. Open PowerShell in that
   folder and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   python main.py
   ```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` directly
for the two `pip` commands and for `main.py`.

## Telegram API credentials and login

Telegram requires your own `api_id` and `api_hash` for an application:

1. Open [my.telegram.org](https://my.telegram.org) and sign in with your
   Telegram account.
2. Choose **API development tools** and create an application.
3. Enter the resulting `api_id` and `api_hash` into EChat on first run.
   Both values are entered without terminal echo and saved in
   `api_credentials.json` in the project folder for later runs.

EChat then asks for your phone number, Telegram login code, and (if enabled)
your two-step verification password. These inputs are hidden while typed and
are not printed by EChat. A local `tg_export_session.session` file is created
after Telegram authorizes the login; future runs reuse it.

Never share `api_credentials.json`, the login code, your two-step password,
or any `*.session*` file. A session file can grant access to your Telegram
account. The API hash is also private. If a session is lost or exposed,
terminate it from Telegram's **Settings → Devices**. Protect the computer or
phone account that owns these files.

## Exporting chats and media

Start with `python main.py`, then choose:

1. **Export one chat**. Select by number; for a long chat list, use `n`/`p`
   to page, `s` to search, or `q` to leave the picker.
2. **Export multiple chats**. Enter chat numbers separated by spaces or
   commas.
3. **Export all chats**.
4. **Exit**.

The app asks whether to download media. If enabled, media is saved under
`exports/<chat-name>/` in folders such as `photos/`, `video_files/`,
`voice_messages/`, and `files/`. `result.json` references downloaded files
with relative paths. If media download is disabled, metadata and Telegram
Desktop-compatible placeholder values are written where applicable.

Exports can contain private conversations and downloaded files. Keep the
whole `exports/` directory private and review it before sharing.

## Proxy troubleshooting

If Telegram connections time out, first check that the network can reach
Telegram. EChat connects directly by default. On Windows only, when no explicit
`TELETHON_PROXY` is set, it reads a manually configured proxy endpoint from
the current user's Windows Internet Settings. It does not evaluate PAC/WPAD
scripts, discover a proxy from a browser, or read Windows proxy settings on
Termux/Linux/macOS.

You can set a proxy explicitly for the current shell. Use the proxy endpoint
and protocol actually provided by your network or VPN:

```powershell
$env:TELETHON_PROXY = "socks5://127.0.0.1:10808"
python main.py
Remove-Item Env:TELETHON_PROXY
```

For Termux, the equivalent shell setting is:

```sh
export TELETHON_PROXY='socks5://127.0.0.1:10808'
python main.py
unset TELETHON_PROXY
```

Supported URL schemes are `socks5://`, `socks4://`, and `http://`. Proxy
credentials can appear in the URL; keep such URLs private and URL-encode
special characters. An explicit environment value overrides the Windows
manual setting. Proxy support uses `python-socks`, included in the dependency
file. EChat reports connection failures without printing the proxy URL or
its credentials.

## Security and privacy

- Credentials, login inputs, sessions, exports, logs, local configuration,
  caches, and generated packages are ignored by `.gitignore`.
- The API credentials and session are saved in the project folder. On POSIX
  systems, the credentials file is restricted to the current user where the
  platform supports those permissions. Windows file access follows the
  folder's Windows ACLs; keep the project in a private user folder.
- Authentication prompts do not echo secrets. EChat does not print the API
  hash, login code, or two-step password. Avoid posting terminal recordings,
  screenshots, or logs that include private chat names or exported data.
- EChat uses Telethon to access Telegram. Telegram's own service, account,
  privacy, and API rules still apply.
- Use **Settings → Devices** in Telegram to end an EChat session when it is no
  longer needed.

For reporting a security issue, see [SECURITY.md](SECURITY.md).

## Known limitations

- Right-to-left Persian/Arabic text may display out of order or without
  shaping in some Termux/terminal combinations. EChat leaves terminal titles
  and exported text unchanged.
- Telegram history and Telethon do not expose every field available to
  Telegram Desktop. For example, recent reaction actor lists are unavailable
  through the fetched history; some peer names depend on Telethon's entity
  cache.
- Stories, album splitting, dice media, and some uncommon/new Telegram object
  types are unsupported or follow Telegram Desktop's TODO behavior.
- Message dates use the local timezone of the device performing the export.
- EChat's JSON aims for Telegram Desktop compatibility, but should be checked
  in the target consumer before being used as a long-term archive format.

## Development and tests

Install the dependencies, then run the test suite from the project root:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests cover callback-byte preservation and Desktop encoding, inline keyboard
ordering, UTF-16 text entity offsets, chronological pagination, JSON
serialization, proxy URL parsing, and narrow/wide chat-picker layouts. See
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a change.

## Roadmap

- Improve coverage for newly available Telegram Desktop fields and Telethon
  constructors.
- Consider support for importing Instagram's downloadable account archive
  as a separate input source, while keeping Telegram export behavior stable.

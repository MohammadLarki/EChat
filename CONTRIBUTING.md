# Contributing

Thanks for considering a contribution. Keep changes focused and preserve the
existing Telegram Desktop JSON conventions.

## Before changing code

- Read `README.md` and `SCHEMA_MAPPING.md`.
- Do not include API credentials, Telegram sessions, exports, media, logs,
  local settings, caches, or generated ZIPs in a contribution.
- Keep changes to terminal display separate from export serialization. The
  narrow-screen chat picker should remain usable without RTL/font packages.
- Do not modify callback bytes. Non-empty callback payloads must round-trip
  exactly through `dataBase64`; do not normalize, decode, or otherwise change
  the source bytes.

## Development

Use Python 3.10 or newer, install the dependencies, and run the existing suite
from the project root:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

For a change that affects exporter output, add or update a focused fixture or
test that describes the compatibility behavior. Never use real account data
in tests. Prefer small synthetic Telegram objects and sanitized fixtures.

## Pull requests

Describe the user-visible change, the relevant compatibility considerations,
and the test command and result. Include screenshots only for visible UI
changes, and ensure they contain no private chat names or account details.

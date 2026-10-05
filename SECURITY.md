# Security policy

## Sensitive files

EChat stores `api_credentials.json` and Telegram's `tg_export_session.session`
in the project directory. Exports and optional downloaded media are stored in
`exports/`. Treat all of these as private. A Telegram session file can grant
account access. Do not commit, upload, or send credentials, login codes,
two-step passwords, session files, exports, or logs containing private data.

EChat hides the API ID, API hash, phone number, login code, and two-step password at
their prompts. It does not print those values. On POSIX, it attempts to set
the credentials file to owner-only permissions. On Windows, access is
controlled by the folder's ACL; use a folder inside your private user profile.

If a session file may have been exposed, revoke the session under Telegram
**Settings → Devices**. If API credentials may have been exposed, replace the
application credentials through Telegram's API development tools when
possible.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** action on the repository's
Security page (private vulnerability reporting/security advisories). Do not
open a public issue with exploit details, credentials, session files, logs,
or exported conversations. Include the affected version, impact, and concise
reproduction steps without account data.

If private reporting is unavailable, open a public issue asking for a private
contact route without including technical exploit details.

## Scope

EChat is a local command-line client that uses Telethon and Telegram's MTProto
API. Vulnerabilities in upstream services or libraries should be reported to
their maintainers as well. This policy does not promise a response time or
security support for unsupported releases.

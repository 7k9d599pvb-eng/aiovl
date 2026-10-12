# aiovl

Experimental asynchronous Python SDK for VLook cloud bots. Python 3.10+, no
third-party runtime dependencies. The current SDK version is `0.0.1.dev0`.
This repository is the standalone SDK; it does not contain the VLook app or
server. It is not a drop-in aiogram fork or a Telegram Bot API client.

Cloud bot conversations are processed by the VLook service and your bot code;
they do **not** use human-chat Matrix E2EE. Bots cannot join human Matrix rooms,
read other conversations, or receive human-chat encryption keys.

## Install

From a checkout of this repository:

```sh
python -m venv .venv
# Activate .venv using your platform's normal command, then:
python -m pip install .
```

Repository: [7k9d599pvb-eng/aiovl](https://github.com/7k9d599pvb-eng/aiovl).
This does not claim a PyPI release. For reproducible deployments, pin the
reviewed Git commit instead of installing a moving branch.

[Russian developer guide](docs/developer-guide-ru.md) covers setup, commands,
keyboards, callbacks, media, acknowledgement, and an example using hidden token
input. The small runnable example is [examples/echo.py](examples/echo.py).

## Configure a bot

1. In VLook Settings, enable developer mode and wait for the save to finish.
2. Open the built-in VLook BotFather (`@vlook_botfather_bot`). Send `/newbot`,
   enter the display name and username, then explicitly confirm creation.
3. Save the issued bot token in your process's secret configuration. Use
   **Скопировать ID для SDK** to obtain the public `botId`. The `@username`
   search handle is not the SDK ID.
4. Configure `VLOOK_BOT_ID`, `VLOOK_BOT_TOKEN`, and `VLOOK_BOT_API_ORIGIN` for
   `examples/echo.py`, or pass them directly to your own application in memory.
   The current primary origin is `https://89.124.86.75`.
5. A user finds the bot by `@username`, opens its private bot chat and sends a
   message such as `/start`. Opening the chat does not send `/start` itself.

Developer mode controls the management UI; users do not need it to chat with
bots. Registration, profile management, token reveal, rotation and revocation
belong to the authenticated VLook application, not this SDK. Never extract an
account session token for bot setup.

Bot tokens are reusable until rotated or revoked. Repeated password-confirmed
token reveal is a separate owner-management operation; it does not consume or
rotate the token. The repeated-reveal server fix passed a bounded check on
12 October 2026. Native Android 0.6.17/build53 contains the updated BotFather
management flow. Earlier hash-only bot records may require an explicit rotation
to issue a recoverable token. Rotate or revoke only after confirmation.

Do not print tokens, private updates or attachment bytes, or put credentials in
URLs, source files or version control. Explicitly set `base_url`: the default
`https://cloud.vlook.invalid` is an unusable placeholder. HTTPS origin validation,
TLS verification and redirect rejection remain enabled. The SDK sends tokens in
the Authorization header. It does not automatically load `.env` files.

## Supported Python API

| API | Current purpose |
| --- | --- |
| `Bot.send_message(bot_id, chat, text, request_id=..., reply_markup=...)` | Private text, up to 4096 characters; optional bounded VLook keyboards. |
| `Bot.get_updates(bot_id, offset=..., limit=100, timeout=0, include_attachments=False)` | Read ordered updates after the exclusive offset; at most 100 per batch. |
| `Bot.acknowledge_updates(bot_id, through)` | Acknowledge a completed prefix separately from polling. |
| `Bot.answer_callback_query(bot_id, callback_query_id, request_id=...)` | Answer a callback; use a stable caller-provided request ID. |
| `Bot.send_photo(bot_id, chat, photo, request_id=..., file_name=..., caption=...)` | Send prepared JPEG/PNG bytes or a bounded local file path. |
| `Bot.send_document(bot_id, chat, document, request_id=..., file_name=..., caption=...)` | Send a PDF; arbitrary file formats are unsupported. |
| `Bot.get_attachment(bot_id, chat, file_id)` | Retrieve bytes and validate the descriptor, requested ID, size, SHA-256 and type signature. |
| `SendMessage`, `GetUpdates`, `AcknowledgeUpdates`, `AnswerCallbackQuery`, `SendPhoto`, `SendDocument`, `GetAttachment` | Method objects with `await method.execute(bot)`. |
| `Router.on_command`, `on_text`, `on_callback`, `on_photo`, `on_document`, `on_attachment` | Ordered, first-match handlers. |
| `Dispatcher.poll_once`, `run_polling`, `stop` | Ordered polling and cancellation; optional acknowledgement, transport retry and attachment capability. |

Use the exact private `chat` object returned in an update. Do not invent a Matrix
room ID or another user's chat ID. The SDK has no owner registration methods,
webhook server, group bot support, payments, web apps, voice/video media, or
full Telegram/aiogram compatibility.

Media methods are implemented and covered by standalone synthetic SDK tests.
That does not establish a completed live media flow in every VLook client.
In particular, the delivered native53 excludes the unfinished native bot-media
layer. Server and client media support must be verified for your workflow before
enabling `include_attachments=True`; its default is `False`.

Photos are JPEG/PNG, documents are PDF, each 1 byte–10 MiB. Photo descriptors
are bounded to 64 million pixels. A caption is at most 1024 UTF-8 bytes, not 1024
characters. Media JSON/base64 bodies are capped at 14 MiB. Supply `file_name`
for byte input; a path's final filename is inferred. The SDK does not compress
photos, stream files, decode image pixels, scan malware, or verify a receipt's
business facts. Bytes and the base64 envelope are held in memory.

## Keyboards, callbacks and polling

`reply_markup` accepts only VLook's exact forms:

```python
{"type": "inline", "rows": [[{"text": "Hello", "callbackData": "hello"}]]}
{"type": "reply", "rows": [[{"text": "Help"}]]}
{"type": "remove"}
```

No URL inline buttons or arbitrary Telegram markup. The contract permits at
most 8 rows, 8 buttons per row, 48 buttons total, 1–64-character labels,
1–64-byte UTF-8 callback data and an 8192-byte normalized markup. Control
characters are rejected. Reply-keyboard taps send that text immediately;
inline buttons submit a callback. Callback answers must complete within the
server's 300-second lifetime before Dispatcher advances or acknowledges them.

Register specific commands/callbacks before generic handlers. With attachments
enabled, register handlers for every required file kind: an unhandled ordinary
update can still advance the cursor. A nonempty attachment caption can fall
back to a text handler when no matching attachment handler exists.

Use `timeout=0`: the current server contract does not provide nonzero long
polling. Dispatcher waits two seconds after an empty batch. `acknowledge=True`
and `retry_transport=True` are optional and off by default. The example enables
both. Retryable reads/acknowledgements get at most five retries after the first
attempt, with waits of 2, 4, 8, 16 and 30 seconds. `stop()` interrupts backoff.
Handlers and their arbitrary side effects are never automatically retried as
transport operations.

Acknowledgement is at-least-once processing, not exactly-once side effects.
A crash after an effect and before acknowledgement can replay an update.
Deduplicate effects durably by `updateId` and reuse the same request ID for a
send/callback retry, including after process restart. SDK cursor and pending
acknowledgement are in memory. Request deduplication has bounded retention.
After an uncertain creation/rotation response, inspect BotFather before
repeating a mutation; do not blindly create another bot or rotate again.

## Standalone tests

After installation, run:

```sh
python -m unittest discover -s tests -p "test_*.py"
```

Tests use only Python's standard library, generated fixtures and injected or
mocked transports. They make no live requests and need no accounts, tokens,
server modules or media files. Passing these tests does not claim service
availability, complete app integration or an independent security audit.

## License and attribution

MIT. The original aiogram copyright notice is preserved in [LICENSE](LICENSE).
[NOTICE.md](NOTICE.md) and [aiovl/UPSTREAM.json](aiovl/UPSTREAM.json) describe the
pinned aiogram reference and the VLook adaptation boundary. No staged aiogram
source snapshot or aiogram runtime dependency is shipped here.

# aiovl

Experimental asynchronous Python SDK for VLook cloud bots. Python 3.10+, no
third-party runtime dependencies. The current SDK version is `0.0.1.dev1`.
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
3. Save the issued bot token in your process's secret configuration.
   No public bot ID is required for new SDK code.
4. Configure only `VLOOK_BOT_TOKEN` in protected process configuration for
   `examples/echo.py`. `Bot(token)` uses the current primary origin
   `https://89.124.86.75`; an explicit HTTPS `base_url` override remains available.
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
URLs, source files or version control. HTTPS origin validation, TLS verification
and redirect rejection remain enabled. There is no host fallback. The SDK sends tokens in
the Authorization header. It does not automatically load `.env` files.

## Quick start: only the bot token

```python
import asyncio
import os
from aiovl import Bot, Dispatcher, Router

async def main():
    bot = Bot(os.environ["VLOOK_BOT_TOKEN"])
    router = Router()

    @router.on_command("/start")
    async def start(update):
        await bot.send_message(update["chat"], "Hello from VLook!",
                               request_id="start:" + update["updateId"])

    await Dispatcher(bot, router=router, acknowledge=True,
                     retry_transport=True).run_polling()

if __name__ == "__main__":
    asyncio.run(main())
```

The first token-only operation authenticates through `POST /v1/cloud-bots/getMe`.
It requires a gateway implementing that endpoint. The SDK strictly accepts only
`{version:1, botId:"bot_…"}` and shares overlapping discovery requests. Only the
public ID is cached; every later operation still authenticates the bearer token.
`await bot.get_me()` performs a fresh auth check and rejects a changed identity.
No owner account token, listing API or copied ID is needed.

On 12 October 2026, a bounded token-only SDK probe passed all 12 checks against
the primary HTTPS API: registration, create/repeated reveal, owner-password
isolation, stable upload retry, attachment isolation, token-only identity,
media dispatch/download/reply, authenticated profile photo roundtrip, real
restart persistence, commands/callbacks with acknowledgement, encrypted backup
restore, and explicit token rotation/revocation. Both QA accounts were verified
deactivated and the bot revoked afterward. This is specific integration evidence,
not a guarantee of service availability, complete client UI or independent audit.

Legacy explicit calls such as `send_message(bot_id, chat, text, request_id=...)`
and `Dispatcher(bot, bot_id, router)` remain supported and do not require
discovery on an older gateway. Method objects retain their old positional order;
new code uses named fields and omits `bot_id`.

## Supported Python API

| API | Current purpose |
| --- | --- |
| `Bot.get_me()` | Authenticate the token and obtain its public bot ID, without profile data. |
| `Bot.send_message(chat, text, request_id=..., reply_markup=...)` | Private text, up to 4096 characters; optional bounded VLook keyboards. |
| `Bot.get_updates(offset=..., limit=100, timeout=0, include_attachments=False)` | Read ordered updates after the exclusive offset; at most 100 per batch. |
| `Bot.acknowledge_updates(through)` | Acknowledge a completed prefix separately from polling. |
| `Bot.answer_callback_query(callback_query_id, request_id=...)` | Answer a callback; use a stable caller-provided request ID. |
| `Bot.send_photo(chat, photo, request_id=..., file_name=..., caption=...)` | Send prepared JPEG/PNG bytes or a bounded local file path. |
| `Bot.send_document(chat, document, request_id=..., file_name=..., caption=...)` | Send a PDF; arbitrary file formats are unsupported. |
| `Bot.get_attachment(chat, file_id)` | Retrieve bytes and validate the descriptor, requested ID, size, SHA-256 and type signature. |
| `GetMe`, `SendMessage`, `GetUpdates`, `AcknowledgeUpdates`, `AnswerCallbackQuery`, `SendPhoto`, `SendDocument`, `GetAttachment` | Method objects with `await method.execute(bot)`. |
| `Router.on_command`, `on_text`, `on_callback`, `on_photo`, `on_document`, `on_attachment` | Ordered, first-match handlers. |
| `Dispatcher.poll_once`, `run_polling`, `stop` | Ordered polling and cancellation; optional acknowledgement, transport retry and attachment capability. |

Use the exact private `chat` object returned in an update. Do not invent a Matrix
room ID or another user's chat ID. The SDK has no owner registration methods,
webhook server, group bot support, payments, web apps, voice/video media, or
full Telegram/aiogram compatibility.

Media methods have standalone synthetic SDK coverage and passed the bounded
SDK-to-primary-HTTPS upload/download/reply probe on 12 October 2026.
That does not establish a completed media flow in every VLook client.
Native53/54 do not implement the user-facing bot-media UI. Client support must
be verified for your workflow before
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
both. Retryable discovery/reads/acknowledgements get at most five retries after the first
attempt, with waits of 2, 4, 8, 16 and 30 seconds. `stop()` interrupts backoff.
Handlers and their arbitrary side effects are never automatically retried as
transport operations.

Acknowledgement is at-least-once processing, not exactly-once side effects.
A crash after an effect and before acknowledgement can replay an update.
Deduplicate effects durably by `updateId` and reuse the same request ID for a
send/callback retry, including after process restart. SDK cursor and pending
acknowledgement are in memory. Request deduplication has bounded retention.
Cancellation, `stop()` or a timeout does not prove that an already submitted
write was rolled back. Retry an uncertain send/callback with the same stable
request ID and unchanged parameters; external effects need their own durable
idempotency key.
After an uncertain creation/rotation response, inspect BotFather before
repeating a mutation; do not blindly create another bot or rotate again.

## Migrating a Telegram / aiogram bot

The [migration section](docs/developer-guide-ru.md#11-перенос-telegram--aiogram-3--aiovl)
maps commands, first-match routers, private chat dictionaries, keyboards,
callback answers, polling/acknowledgement and JPEG/PNG/PDF methods. Telegram
tokens, numeric chat IDs, file IDs and markup objects cannot be reused. Group,
webhook, payment, web app, edit/delete/forward and full aiogram framework features
are unsupported. Port the supported business logic and test it before live use;
this is not an automatic import replacement or a general-availability promise.

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

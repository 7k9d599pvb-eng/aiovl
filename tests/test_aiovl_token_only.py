"""Token-only identity, cancellation and legacy compatibility; no real accounts."""
import asyncio
import base64
import hashlib
import json
import secrets
import unittest
from unittest.mock import patch

from aiovl import (
    AcknowledgeUpdates, AnswerCallbackQuery, Bot, CloudBotTransportError,
    CloudBotValidationError, Dispatcher, GetAttachment, GetMe, GetUpdates,
    Router, SendDocument, SendMessage, SendPhoto,
)

BOT = "bot_synthetic_01"
CHAT = {"kind": "private", "id": "chat_synthetic_01"}
CALLBACK = "callback_" + "a" * 32
FILE = "media_" + "b" * 32


def identity(bot_id=BOT):
    return {"ok": True, "result": {"version": 1, "botId": bot_id}}


class TokenOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_only_send_uses_exact_discovery_and_header(self):
        token, calls = secrets.token_hex(24), []
        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            self.assertEqual(method, "POST")
            self.assertEqual(headers["Authorization"], "Bearer " + token)
            self.assertNotIn(token, path)
            self.assertNotIn(token, body.decode())
            calls.append((path, payload))
            return identity() if path.endswith("getMe") else {"ok": True, "result": {"botId": BOT}}
        bot = Bot(token, transport=transport)
        await bot.send_message(CHAT, "Hello", request_id="reply:1")
        await bot.send_message(chat=CHAT, text="Again", request_id="reply:2")
        self.assertEqual(calls[0], ("/v1/cloud-bots/getMe", {"version": 1, "operation": "getMe"}))
        self.assertEqual([path for path, _ in calls].count("/v1/cloud-bots/getMe"), 1)
        self.assertTrue(all(payload["botId"] == BOT for _, payload in calls[1:]))
        self.assertNotIn(token, repr(bot))

    async def test_default_origin_and_explicit_override_keep_tls_transport(self):
        seen = []
        async def transport(origin, token, method, path, headers, body, timeout, limit):
            seen.append((origin, path))
            return identity()
        with patch("aiovl.client._https_transport", transport):
            await Bot(secrets.token_hex(24)).get_me()
            await Bot(secrets.token_hex(24), base_url="https://example.invalid").get_me()
        self.assertEqual(seen, [("https://89.124.86.75", "/v1/cloud-bots/getMe"),
                                ("https://example.invalid", "/v1/cloud-bots/getMe")])

    async def test_legacy_explicit_calls_do_not_require_new_endpoint(self):
        calls = []
        async def transport(method, path, headers, body, timeout):
            calls.append(path)
            self.assertNotEqual(path, "/v1/cloud-bots/getMe")
            return {"ok": True, "result": [] if path.endswith("getUpdates") else {"accepted": True}}
        bot = Bot(secrets.token_hex(24), transport=transport)
        await bot.send_message(BOT, CHAT, "Legacy", request_id="legacy:1")
        await SendMessage(BOT, CHAT, "Legacy object", "legacy:2").execute(bot)
        await GetUpdates(BOT).execute(bot)
        self.assertEqual(len(calls), 3)

    async def test_concurrent_token_only_operations_share_discovery(self):
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def transport(method, path, headers, body, timeout):
            nonlocal count
            if path.endswith("getMe"):
                count += 1
                entered.set()
                await release.wait()
                return identity()
            return {"ok": True, "result": []}
        bot = Bot(secrets.token_hex(24), transport=transport)
        first = asyncio.create_task(bot.get_updates())
        await entered.wait()
        second = asyncio.create_task(bot.get_updates())
        await asyncio.sleep(0)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), [[], []])
        self.assertEqual(count, 1)

    async def test_cancelled_waiter_does_not_cancel_another_discovery_waiter(self):
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def transport(method, path, headers, body, timeout):
            nonlocal count
            count += 1
            entered.set()
            await release.wait()
            return identity()
        bot = Bot(secrets.token_hex(24), transport=transport)
        first = asyncio.create_task(bot.get_me())
        await entered.wait()
        second = asyncio.create_task(bot.get_me())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual(await second, {"version": 1, "botId": BOT})
        self.assertEqual(count, 1)

    async def test_failed_discovery_can_retry_without_poisoned_cache_or_detail(self):
        count = 0
        token = secrets.token_hex(24)
        async def transport(method, path, headers, body, timeout):
            nonlocal count
            count += 1
            if count == 1:
                raise CloudBotTransportError(token, retryable=True)
            return identity()
        bot = Bot(token, transport=transport)
        with self.assertRaises(CloudBotTransportError) as caught:
            await bot.get_me()
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn(token, str(caught.exception))
        self.assertIsNone(bot._resolved_bot_id)
        self.assertEqual(await bot.get_me(), {"version": 1, "botId": BOT})

    async def test_get_me_rejects_schema_version_identity_and_extra_fields(self):
        results = [None, [], {}, {"version": True, "botId": BOT},
                   {"version": 2, "botId": BOT}, {"version": 1, "botId": "@wrong_bot"},
                   {"version": 1, "botId": BOT, "token": "synthetic-sensitive"},
                   {"version": 1, "botId": BOT, "displayName": "Unexpected"}]
        for result in results:
            async def transport(*args):
                return {"ok": True, "result": result}
            with self.subTest(result_type=type(result).__name__):
                bot = Bot(secrets.token_hex(24), transport=transport)
                with self.assertRaises(CloudBotTransportError):
                    await bot.get_me()
                self.assertIsNone(bot._resolved_bot_id)

    async def test_get_me_reauthenticates_and_rejects_rebound_identity(self):
        count = 0
        async def transport(*args):
            nonlocal count
            count += 1
            return identity(BOT if count == 1 else "bot_other_01")
        bot = Bot(secrets.token_hex(24), transport=transport)
        await bot.get_me()
        with self.assertRaises(CloudBotTransportError):
            await bot.get_me()
        self.assertEqual(count, 2)
        self.assertEqual(bot._resolved_bot_id, BOT)

    async def test_cached_identity_does_not_bypass_revocation(self):
        revoked, calls = False, []
        async def transport(method, path, headers, body, timeout):
            calls.append(path)
            if revoked:
                return {"ok": False, "description": "synthetic-sensitive"}
            return identity() if path.endswith("getMe") else {"ok": True, "result": []}
        bot = Bot(secrets.token_hex(24), transport=transport)
        await bot.get_updates()
        revoked = True
        for operation in (bot.get_updates, bot.get_me):
            with self.assertRaises(CloudBotTransportError) as caught:
                await operation()
            self.assertFalse(caught.exception.retryable)
            self.assertNotIn("synthetic-sensitive", str(caught.exception))
        self.assertEqual(calls, ["/v1/cloud-bots/getMe", "/v1/cloud-bots/getUpdates",
                                "/v1/cloud-bots/getUpdates", "/v1/cloud-bots/getMe"])

    async def test_authenticated_identity_rejects_explicit_other_id(self):
        calls = []
        async def transport(method, path, headers, body, timeout):
            calls.append(path)
            return identity()
        bot = Bot(secrets.token_hex(24), transport=transport)
        await bot.get_me()
        with self.assertRaises(CloudBotValidationError):
            await bot.send_message("bot_other_01", CHAT, "Wrong", request_id="wrong:1")
        self.assertEqual(calls, ["/v1/cloud-bots/getMe"])

    async def test_invalid_token_only_inputs_fail_before_discovery(self):
        calls = []
        async def transport(*args):
            calls.append(True)
            raise AssertionError("No request expected")
        bot = Bot(secrets.token_hex(24), transport=transport)
        operations = [bot.send_message(CHAT, "", request_id="bad:1"),
                      bot.send_message({"kind": "group", "id": CHAT["id"]}, "x", request_id="bad:2"),
                      bot.send_message(CHAT, CHAT, "ambiguous", request_id="bad:3"),
                      bot.get_updates(limit=101), bot.get_updates(request_timeout=False),
                      bot.get_me(timeout=0), bot.acknowledge_updates("01"),
                      bot.answer_callback_query("invalid", request_id="bad:4"),
                      bot.send_document(CHAT, b"bad", request_id="bad:5", file_name="bad.pdf")]
        for operation in operations:
            with self.assertRaises(CloudBotValidationError):
                await operation
        self.assertFalse(calls)

    async def test_token_only_dispatcher_commands_callbacks_and_ack(self):
        calls = []
        updates = [{"version": 1, "updateId": "1", "botId": BOT, "chat": CHAT, "text": "/start"},
                   {"version": 1, "updateId": "2", "botId": BOT, "chat": CHAT,
                    "callbackQuery": {"id": CALLBACK, "messageId": "1", "data": "hello"}}]
        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            calls.append(path)
            if path.endswith("getMe"):
                return identity()
            if path.endswith("getUpdates"):
                result = updates if "offset" not in payload else []
            elif path.endswith("answerCallbackQuery"):
                result = {"answered": True, "callbackQueryId": CALLBACK}
            elif path.endswith("acknowledgeUpdates"):
                result = {"acknowledgedThrough": payload["through"]}
            else:
                result = {"botId": BOT}
            return {"ok": True, "result": result}
        bot, router = Bot(secrets.token_hex(24), transport=transport), Router()
        @router.on_command("/start")
        async def start(update):
            await bot.send_message(update["chat"], "Welcome", request_id="start:1")
        @router.on_callback("hello")
        async def callback(update):
            await bot.answer_callback_query(update["callbackQuery"]["id"], request_id="callback:2")
        dispatcher = Dispatcher(bot, router=router, acknowledge=True)
        self.assertEqual(await dispatcher.poll_once(), 2)
        self.assertEqual(dispatcher.offset, "2")
        self.assertEqual(await dispatcher.poll_once(), 0)
        self.assertEqual(calls.count("/v1/cloud-bots/getMe"), 1)
        self.assertEqual(Dispatcher(bot, router).router, router)

    async def test_token_only_dispatcher_rejects_cross_bot_updates_before_handlers(self):
        handled = []
        async def transport(method, path, headers, body, timeout):
            return identity() if path.endswith("getMe") else {"ok": True, "result": [
                {"version": 1, "updateId": "1", "botId": "bot_other_01", "chat": CHAT, "text": "x"}]}
        router = Router()
        router.on_text(lambda update: handled.append(True))
        dispatcher = Dispatcher(Bot(secrets.token_hex(24), transport=transport), router=router, acknowledge=True)
        with self.assertRaises(CloudBotValidationError):
            await dispatcher.poll_once()
        self.assertFalse(handled)
        self.assertIsNone(dispatcher.offset)

    async def test_token_only_media_and_method_objects_preserve_boundaries(self):
        pdf = b"%PDF-1.7\nsynthetic\n%%EOF"
        png = b"\x89PNG\r\n\x1a\nsynthetic"  # SDK signature fixture, not an image-decoder claim.
        descriptor = {"fileId": FILE, "kind": "document", "fileName": "receipt.pdf",
                      "mimeType": "application/pdf", "size": len(pdf), "sha256": hashlib.sha256(pdf).hexdigest()}
        calls = []
        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            calls.append(path)
            if path.endswith("getMe"):
                return identity()
            self.assertEqual(payload["botId"], BOT)
            if path.endswith("botGetAttachment"):
                result = {**descriptor, "data": base64.b64encode(pdf).decode()}
            else:
                data = base64.b64decode(payload["data"], validate=True)
                attachment = {"fileId": FILE, "kind": payload["kind"], "fileName": payload["fileName"],
                              "mimeType": payload["mimeType"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                if payload["kind"] == "photo":
                    attachment.update(width=2, height=3)
                result = {"version": 1, "messageId": "1", "botId": BOT, "chat": CHAT,
                          "text": payload["caption"], "attachment": attachment}
            return {"ok": True, "result": result}
        bot = Bot(secrets.token_hex(24), transport=transport)
        await bot.send_document(CHAT, pdf, request_id="document:1", file_name="receipt.pdf")
        await bot.send_photo(CHAT, png, request_id="photo:1", file_name="image.png")
        await SendDocument(chat=CHAT, document=pdf, request_id="document:2", file_name="receipt.pdf").execute(bot)
        await SendPhoto(chat=CHAT, photo=png, request_id="photo:2", file_name="image.png").execute(bot)
        self.assertEqual((await bot.get_attachment(CHAT, FILE))["data"], pdf)
        self.assertEqual((await GetAttachment(chat=CHAT, file_id=FILE).execute(bot))["data"], pdf)
        self.assertEqual(calls.count("/v1/cloud-bots/getMe"), 1)

    async def test_token_only_keyword_methods_and_get_me_object(self):
        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            if path.endswith("getMe"):
                return identity()
            if path.endswith("getUpdates"):
                result = []
            elif path.endswith("answerCallbackQuery"):
                result = {"answered": True, "callbackQueryId": payload["callbackQueryId"]}
            elif path.endswith("acknowledgeUpdates"):
                result = {"acknowledgedThrough": payload["through"]}
            else:
                result = {"botId": BOT}
            return {"ok": True, "result": result}
        bot = Bot(secrets.token_hex(24), transport=transport)
        self.assertEqual(await GetMe().execute(bot), {"version": 1, "botId": BOT})
        await SendMessage(chat=CHAT, text="Hello", request_id="reply:1").execute(bot)
        self.assertEqual(await GetUpdates().execute(bot), [])
        await AnswerCallbackQuery(callback_query_id=CALLBACK, request_id="answer:1").execute(bot)
        await bot.answer_callback_query(callback_query_id=CALLBACK, request_id="answer:2")
        self.assertEqual(await AcknowledgeUpdates(through="2").execute(bot), "2")
        self.assertEqual(await bot.acknowledge_updates(through="2"), "2")

    async def test_discovery_timeout_is_bounded_and_never_falls_back_to_listing(self):
        calls = []
        async def transport(method, path, headers, body, timeout):
            calls.append(path)
            await asyncio.sleep(1)
        bot = Bot(secrets.token_hex(24), transport=transport)
        with self.assertRaises(CloudBotTransportError) as caught:
            await bot.get_updates(request_timeout=0.01)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(calls, ["/v1/cloud-bots/getMe"])
        await asyncio.sleep(0.02)
        self.assertIsNone(bot._resolved_bot_id)
        self.assertIsNone(bot._identity_task)


if __name__ == "__main__":
    unittest.main()

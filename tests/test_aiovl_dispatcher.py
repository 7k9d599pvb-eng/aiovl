import asyncio
import unittest

from aiovl import Bot, CloudBotValidationError
from aiovl.dispatcher import Dispatcher, Router


def update(update_id="1", text="hello"):
    return {"version": 1, "updateId": update_id, "botId": "bot_weather_01", "chat": {"kind": "private", "id": "chat_alice"}, "text": text}


class DispatcherTests(unittest.TestCase):
    def test_ordered_handlers_and_offset_after_success(self):
        batches = [[update("1", "/start now"), update("2", "hello")], [update("2", "hello")], []]
        calls, seen = [], []
        async def transport(method, path, headers, body, timeout):
            calls.append(__import__("json").loads(body))
            return {"ok": True, "result": batches.pop(0)}
        router = Router()
        @router.on_command("/start")
        async def command(item): seen.append(("command", item["updateId"]))
        @router.on_text
        async def text(item): seen.append(("text", item["updateId"]))
        async def run():
            d = Dispatcher(Bot("synthetic-token-0123456789", transport=transport), "bot_weather_01", router, timeout=1)
            self.assertEqual(await d.poll_once(), 2)
            self.assertEqual(d.offset, "2")
            self.assertEqual(await d.poll_once(), 0)
            return d
        asyncio.run(run())
        self.assertEqual(seen, [("command", "1"), ("text", "2")])
        self.assertEqual(calls[0].get("offset"), None)
        self.assertEqual(calls[1].get("offset"), "2")

    def test_handler_failure_does_not_advance_or_get_silently_retried(self):
        batches = [[update("7", "boom")]]
        async def transport(method, path, headers, body, timeout):
            return {"ok": True, "result": batches.pop(0)}
        router = Router()
        @router.on_text
        async def fail(item): raise RuntimeError("handler failed")
        async def run():
            d = Dispatcher(Bot("synthetic-token-0123456789", transport=transport), "bot_weather_01", router)
            with self.assertRaisesRegex(RuntimeError, "handler failed"):
                await d.poll_once()
            self.assertIsNone(d.offset)
        asyncio.run(run())

    def test_cancellation_propagates_and_stop_ends_loop(self):
        async def transport(*args):
            await asyncio.sleep(10)
        async def run():
            d = Dispatcher(Bot("synthetic-token-0123456789", transport=transport), "bot_weather_01")
            task = asyncio.create_task(d.run_polling())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        asyncio.run(run())

    def test_whitespace_text_is_safe_and_batch_order_is_strict(self):
        async def transport(method, path, headers, body, timeout):
            return {"ok": True, "result": [update("1", "   "), update("1", "again")]}
        async def run():
            d = Dispatcher(Bot("synthetic-token-0123456789", transport=transport), "bot_weather_01")
            with self.assertRaises(CloudBotValidationError):
                await d.poll_once()
        asyncio.run(run())

    def test_stop_interrupts_polling_without_swallowing_handler_errors(self):
        holder = {}
        async def transport(method, path, headers, body, timeout):
            holder["dispatcher"].stop()
            await asyncio.sleep(10)
        async def run():
            d = Dispatcher(Bot("synthetic-token-0123456789", transport=transport), "bot_weather_01")
            holder["dispatcher"] = d
            await d.run_polling()
        asyncio.run(run())

    def test_malformed_or_other_bot_updates_rejected(self):
        async def transport(method, path, headers, body, timeout):
            return {"ok": True, "result": [update("1") | {"botId": "bot_other_01"}]}
        async def run():
            d = Dispatcher(Bot("synthetic-token-0123456789", transport=transport), "bot_weather_01")
            with self.assertRaises(CloudBotValidationError):
                await d.poll_once()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

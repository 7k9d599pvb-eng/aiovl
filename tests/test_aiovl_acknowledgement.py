import asyncio
import json
import unittest
from unittest.mock import patch

from aiovl import AcknowledgeUpdates, Bot, CloudBotTransportError, CloudBotValidationError
from aiovl.dispatcher import Dispatcher, Router


BOT = "bot_weather_01"
TOKEN = "synthetic-token-0123456789"


def retryable_transport_error():
    error = CloudBotTransportError("synthetic transient transport failure")
    error.retryable = True
    return error


def update(number, text=None):
    return {"version": 1, "updateId": str(number), "botId": BOT,
            "chat": {"kind": "private", "id": "chat_alice"},
            "text": text or f"message {number}"}


class AcknowledgeClientTests(unittest.TestCase):
    def test_acknowledgement_uses_bounded_endpoint_contract(self):
        calls = []

        async def transport(method, path, headers, body, timeout):
            calls.append((method, path, headers, json.loads(body), timeout))
            return {"ok": True, "result": {"acknowledgedThrough": "17"}}

        async def run():
            bot = Bot(TOKEN, transport=transport)
            self.assertEqual(await bot.acknowledge_updates(BOT, "17"), "17")
            self.assertEqual(await AcknowledgeUpdates(BOT, "17").execute(bot), "17")

        asyncio.run(run())
        self.assertEqual(len(calls), 2)
        for method, path, headers, body, timeout in calls:
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/v1/cloud-bots/acknowledgeUpdates")
            self.assertEqual(body, {"version": 1, "operation": "acknowledgeUpdates",
                                    "botId": BOT, "through": "17"})
            self.assertEqual(timeout, 30)
            self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_acknowledgement_rejects_invalid_inputs_and_response_shape(self):
        async def run():
            bot = Bot(TOKEN, transport=lambda *_: asyncio.sleep(0, result={
                "ok": True, "result": {"acknowledgedThrough": "01"}}))
            with self.assertRaises(CloudBotValidationError):
                await bot.acknowledge_updates(BOT, "01")
            with self.assertRaises(CloudBotTransportError):
                await bot.acknowledge_updates(BOT, "1")

            for response in (
                {"ok": True, "result": {"acknowledgedThrough": "1", "extra": True}},
                {"ok": True, "result": {"acknowledgedThrough": 1}},
                {"ok": True, "result": {"acknowledgedThrough": "9223372036854775808"}},
            ):
                bot._transport = lambda *_, value=response: asyncio.sleep(0, result=value)
                with self.assertRaises(CloudBotTransportError):
                    await bot.acknowledge_updates(BOT, "1")

        asyncio.run(run())


class AcknowledgeDispatcherTests(unittest.TestCase):
    def test_ack_occurs_only_after_handler_and_get_updates_offset_stays_exclusive(self):
        trace, handled = [], []
        batch = [update(4), update(5)]

        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            trace.append((path.rsplit("/", 1)[-1], payload))
            if path.endswith("getUpdates"):
                return {"ok": True, "result": batch if payload.get("offset") is None else []}
            return {"ok": True, "result": {"acknowledgedThrough": payload["through"]}}

        router = Router()

        @router.on_text
        async def handler(item):
            handled.append(item["updateId"])

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router,
                                    acknowledge=True)
            self.assertEqual(await dispatcher.poll_once(), 2)
            self.assertEqual(dispatcher.offset, "5")

        asyncio.run(run())
        self.assertEqual(handled, ["4", "5"])
        self.assertEqual([item[0] for item in trace], ["getUpdates", "acknowledgeUpdates"])
        self.assertEqual(trace[0][1].get("offset"), None)
        self.assertEqual(trace[1][1]["through"], "5")

    def test_failed_handler_does_not_ack_or_advance_failed_update(self):
        calls, handled = [], []
        batches = [[update(1), update(2)], [update(2)]]
        should_fail = True

        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            calls.append((path.rsplit("/", 1)[-1], payload))
            if path.endswith("getUpdates"):
                return {"ok": True, "result": batches.pop(0)}
            return {"ok": True, "result": {"acknowledgedThrough": payload["through"]}}

        router = Router()

        @router.on_text
        async def handler(item):
            nonlocal should_fail
            handled.append(item["updateId"])
            if item["updateId"] == "2" and should_fail:
                should_fail = False
                raise RuntimeError("handler failed")

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router,
                                    acknowledge=True)
            with self.assertRaisesRegex(RuntimeError, "handler failed"):
                await dispatcher.poll_once()
            self.assertIsNone(dispatcher.offset)
            self.assertEqual(dispatcher._pending_ack, "1")
            self.assertEqual([name for name, _ in calls], ["getUpdates"])
            self.assertEqual(await dispatcher.poll_once(), 1)
            self.assertEqual(dispatcher.offset, "2")

        asyncio.run(run())
        self.assertEqual(handled, ["1", "2", "2"])
        self.assertEqual([name for name, _ in calls],
                         ["getUpdates", "acknowledgeUpdates", "getUpdates", "acknowledgeUpdates"])
        self.assertEqual([payload["through"] for name, payload in calls
                          if name == "acknowledgeUpdates"], ["1", "2"])
        self.assertEqual([payload.get("offset") for name, payload in calls
                          if name == "getUpdates"], [None, "1"])

    def test_lost_ack_response_retries_same_ack_without_rerunning_handler(self):
        calls, handled = [], []
        fail_first_ack = True

        async def transport(method, path, headers, body, timeout):
            nonlocal fail_first_ack
            payload = json.loads(body)
            name = path.rsplit("/", 1)[-1]
            calls.append((name, payload))
            if name == "getUpdates":
                return {"ok": True, "result": [update(9)] if payload.get("offset") is None else []}
            if fail_first_ack:
                fail_first_ack = False
                raise TimeoutError("synthetic lost acknowledgement response")
            return {"ok": True, "result": {"acknowledgedThrough": payload["through"]}}

        router = Router()

        @router.on_text
        async def handler(item):
            handled.append(item["updateId"])

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router,
                                    acknowledge=True)
            with self.assertRaises(CloudBotTransportError):
                await dispatcher.poll_once()
            self.assertIsNone(dispatcher.offset)
            self.assertEqual(dispatcher._pending_ack, "9")
            self.assertEqual(await dispatcher.poll_once(), 0)
            self.assertEqual(dispatcher.offset, "9")
            self.assertIsNone(dispatcher._pending_ack)

        asyncio.run(run())
        self.assertEqual(handled, ["9"])
        self.assertEqual([name for name, _ in calls],
                         ["getUpdates", "acknowledgeUpdates", "acknowledgeUpdates", "getUpdates"])
        self.assertEqual([payload["through"] for name, payload in calls
                          if name == "acknowledgeUpdates"], ["9", "9"])
        self.assertEqual(calls[-1][1].get("offset"), "9")

    def test_acknowledgement_is_opt_in_for_compatibility(self):
        calls = []

        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            calls.append(path.rsplit("/", 1)[-1])
            return {"ok": True, "result": [update(3)] if path.endswith("getUpdates") else {}}

        async def run():
            router = Router()
            router.on_text(lambda item: None)
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router)
            await dispatcher.poll_once()
            self.assertEqual(dispatcher.offset, "3")

        asyncio.run(run())
        self.assertEqual(calls, ["getUpdates"])


class DispatcherTransportRetryTests(unittest.TestCase):
    def test_retryable_poll_failure_retries_read_and_dispatches_once(self):
        requests, handled = [], []

        async def transport(method, path, headers, body, timeout):
            requests.append(path.rsplit("/", 1)[-1])
            if len(requests) == 1:
                raise retryable_transport_error()
            return {"ok": True, "result": [update(1)]}

        router = Router()
        router.on_text(lambda item: handled.append(item["updateId"]))

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router,
                                    retry_transport=True)
            self.assertEqual(await dispatcher.poll_once(), 1)
            self.assertEqual(dispatcher.offset, "1")

        asyncio.run(run())
        self.assertEqual(requests, ["getUpdates", "getUpdates"])
        self.assertEqual(handled, ["1"])

    def test_retryable_ack_failure_retries_ack_without_repeating_handler(self):
        requests, handled = [], []

        async def transport(method, path, headers, body, timeout):
            name = path.rsplit("/", 1)[-1]
            payload = json.loads(body)
            requests.append((name, payload))
            if name == "getUpdates":
                return {"ok": True, "result": [update(8)]}
            if sum(name == "acknowledgeUpdates" for name, _ in requests) == 1:
                raise retryable_transport_error()
            return {"ok": True, "result": {"acknowledgedThrough": payload["through"]}}

        router = Router()
        router.on_text(lambda item: handled.append(item["updateId"]))

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router,
                                    acknowledge=True, retry_transport=True)
            self.assertEqual(await dispatcher.poll_once(), 1)
            self.assertEqual(dispatcher.offset, "8")
            self.assertIsNone(dispatcher._pending_ack)

        asyncio.run(run())
        self.assertEqual(handled, ["8"])
        self.assertEqual([name for name, _ in requests],
                         ["getUpdates", "acknowledgeUpdates", "acknowledgeUpdates"])
        self.assertEqual([payload["through"] for name, payload in requests
                          if name == "acknowledgeUpdates"], ["8", "8"])

    def test_handler_transport_error_is_not_retried_as_poll_transport(self):
        reads, handled = [], []

        async def transport(method, path, headers, body, timeout):
            reads.append(path)
            return {"ok": True, "result": [update(2)]}

        router = Router()

        async def handler(item):
            handled.append(item["updateId"])
            raise retryable_transport_error()

        router.on_text(handler)

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, router,
                                    retry_transport=True)
            with self.assertRaises(CloudBotTransportError):
                await dispatcher.poll_once()
            self.assertIsNone(dispatcher.offset)

        asyncio.run(run())
        self.assertEqual(handled, ["2"])
        self.assertEqual(reads, ["/v1/cloud-bots/getUpdates"])

    def test_retry_bound_and_permanent_errors(self):
        async def run():
            for enabled, transient, expected in ((True, True, 6), (True, False, 1), (False, True, 1)):
                calls = []
                async def transport(*args):
                    calls.append(1)
                    raise CloudBotTransportError("synthetic", retryable=transient)
                dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT, retry_transport=enabled)
                with patch("aiovl.dispatcher._TRANSPORT_RETRY_DELAYS", (0.001,) * 5):
                    with self.assertRaises(CloudBotTransportError):
                        await dispatcher.poll_once()
                self.assertEqual(len(calls), expected)
                self.assertIsNone(dispatcher.offset)
        asyncio.run(run())

    def test_stop_interrupts_retry_backoff(self):
        entered = asyncio.Event()
        requests = []

        async def transport(method, path, headers, body, timeout):
            requests.append(path)
            entered.set()
            raise retryable_transport_error()

        async def run():
            dispatcher = Dispatcher(Bot(TOKEN, transport=transport), BOT,
                                    retry_transport=True)
            task = asyncio.create_task(dispatcher.run_polling())
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.sleep(0)
            dispatcher.stop()
            await asyncio.wait_for(task, timeout=1)

        asyncio.run(run())
        self.assertEqual(requests, ["/v1/cloud-bots/getUpdates"])


if __name__ == "__main__":
    unittest.main()

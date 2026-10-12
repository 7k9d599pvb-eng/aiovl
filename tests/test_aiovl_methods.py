import asyncio
import unittest

from aiovl import Bot, CloudBotValidationError
from aiovl.methods import GetUpdates, SendMessage


class AioVlookMethodTests(unittest.TestCase):
    def test_methods_delegate_to_validated_header_transport(self):
        calls = []

        async def transport(method, path, headers, body, timeout):
            calls.append((method, path, headers, body))
            if path.endswith("sendText"):
                return {"ok": True, "result": {"accepted": True}}
            return {"ok": True, "result": []}

        async def run():
            bot = Bot("synthetic-token-0123456789", transport=transport)
            sent = await SendMessage("bot_weather_01", {"kind": "private", "id": "chat_alice"}, " hello\n", "req_1").execute(bot)
            updates = await GetUpdates("bot_weather_01", limit=2).execute(bot)
            return sent, updates

        sent, updates = asyncio.run(run())
        self.assertEqual(sent, {"accepted": True})
        self.assertEqual(updates, [])
        self.assertEqual([c[1] for c in calls], ["/v1/cloud-bots/sendText", "/v1/cloud-bots/getUpdates"])
        self.assertEqual(calls[0][2]["Authorization"], "Bearer synthetic-token-0123456789")
        self.assertNotIn("synthetic-token", calls[0][3].decode())

    def test_method_objects_do_not_accept_unsupported_surface_or_bad_result(self):
        async def transport(*args):
            return {"ok": True, "result": {"not": "a-list"}}

        async def run():
            with self.assertRaises(CloudBotValidationError):
                await SendMessage("bot_weather_01", {"kind": "group", "id": "chat_group"}, "x", "req_1").execute(Bot("synthetic-token-0123456789", transport=transport))
            with self.assertRaises(CloudBotValidationError):
                await GetUpdates("bot_weather_01").execute(Bot("synthetic-token-0123456789", transport=transport))

        asyncio.run(run())

    def test_compatibility_method_names_are_explicit(self):
        self.assertEqual(SendMessage.__api_method__, "sendMessage")
        self.assertEqual(GetUpdates.__api_method__, "getUpdates")


if __name__ == "__main__":
    unittest.main()

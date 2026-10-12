import asyncio
import unittest

from aiovl import Bot, CloudBotTransportError, CloudBotValidationError


class AioVlookTests(unittest.TestCase):
    def test_injected_transport_uses_header_and_fixed_paths(self):
        calls = []

        async def transport(method, path, headers, body, timeout):
            calls.append((method, path, headers, body, timeout))
            return {"ok": True, "result": {"accepted": True}}

        async def run():
            bot = Bot("synthetic-token-0123456789", transport=transport)
            await bot.send_message("bot_weather_01", {"kind": "private", "id": "chat_alice"}, "hello\nworld", request_id="req_1")
            await bot.get_updates("bot_weather_01", offset="4")

        asyncio.run(run())
        self.assertEqual([call[1] for call in calls], ["/v1/cloud-bots/sendText", "/v1/cloud-bots/getUpdates"])
        self.assertEqual(calls[0][2]["Authorization"], "Bearer synthetic-token-0123456789")
        self.assertNotIn("synthetic-token", calls[0][3].decode())

    def test_private_only_bounds_and_unknown_input(self):
        async def run():
            bot = Bot("synthetic-token-0123456789", transport=lambda *args: None)
            with self.assertRaises(CloudBotValidationError):
                await bot.send_message("bot_weather_01", {"kind": "group", "id": "chat_group"}, "x", request_id="r")
            with self.assertRaises(CloudBotValidationError):
                await bot.send_message("bot_weather_01", {"kind": "private", "id": "chat_alice"}, "x\x00", request_id="r")
            with self.assertRaises(CloudBotValidationError):
                await bot.get_updates("bot_weather_01", limit=101)
            with self.assertRaises(CloudBotValidationError):
                await bot.get_updates("bot_weather_01", offset="01")

        asyncio.run(run())

    def test_endpoint_credentials_and_redirects_are_rejected(self):
        from aiovl.client import _NoRedirect
        for endpoint in ["http://example.org", "https://user:pass@example.org", "https://example.org/secret", "https://example.org:bad", "https://example.org?token=x"]:
            with self.assertRaises(CloudBotValidationError):
                Bot("synthetic-token-0123456789", base_url=endpoint)
        with self.assertRaises(CloudBotValidationError):
            Bot("synthetic token 0123456789")
        with self.assertRaises(CloudBotTransportError):
            _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid")

    def test_timeout_and_sanitized_errors(self):
        async def slow(*args):
            await asyncio.sleep(1)

        async def run():
            bot = Bot("synthetic-token-0123456789", transport=slow)
            with self.assertRaises(CloudBotTransportError) as error:
                await bot.get_updates("bot_weather_01", request_timeout=0.01)
            self.assertNotIn("synthetic-token", str(error.exception))
            self.assertNotIn("synthetic-token", repr(bot))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

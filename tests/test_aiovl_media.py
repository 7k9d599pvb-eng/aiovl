import base64
import hashlib
import json
import secrets
import unittest
import struct
import zlib

from aiovl import Bot, CloudBotTransportError, CloudBotValidationError, GetAttachment, SendDocument, SendPhoto
from aiovl.dispatcher import _update


def _png():
    # A complete 2 x 3 RGB PNG generated using only the standard library.
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    header = struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes((21, 42, 63)) * 2) * 3
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _token():
    return secrets.token_hex(24)


class AioVLookMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_photo_uses_header_and_bounded_base64_contract(self):
        data = _png()
        seen = []
        descriptor = {"fileId": "media_" + secrets.token_hex(16), "kind": "photo",
                     "fileName": "receipt.png", "mimeType": "image/png", "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest(), "width": 2, "height": 3}

        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            seen.append((method, path, headers, payload))
            return {"ok": True, "result": {"version": 1, "messageId": "4", "botId": "bot_test_media_01",
                    "chat": {"kind": "private", "id": "chat_test_media_01"}, "text": "", "attachment": descriptor}}

        token = _token()
        bot = Bot(token, transport=transport, base_url="https://example.invalid")
        result = await bot.send_photo("bot_test_media_01", {"kind": "private", "id": "chat_test_media_01"},
                                      data, request_id="stable-media-1", file_name="receipt.png")
        self.assertEqual(result["attachment"], descriptor)
        method, path, headers, payload = seen[0]
        self.assertEqual((method, path), ("POST", "/v1/cloud-bots/botSendAttachment"))
        self.assertEqual(headers["Authorization"], "Bearer " + token)
        self.assertNotIn(token, path)
        self.assertEqual(base64.b64decode(payload["data"], validate=True), data)
        self.assertEqual(payload["requestId"], "stable-media-1")
        self.assertEqual(payload["kind"], "photo")

    async def test_fetch_authenticates_descriptor_and_hash(self):
        data = _png()
        file_id = "media_" + secrets.token_hex(16)
        descriptor = {"fileId": file_id, "kind": "photo", "fileName": "receipt.png",
                      "mimeType": "image/png", "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "width": 2, "height": 3}

        async def transport(method, path, headers, body, timeout):
            self.assertEqual(path, "/v1/cloud-bots/botGetAttachment")
            self.assertNotIn("?", path)
            return {"ok": True, "result": {**descriptor, "data": base64.b64encode(data).decode("ascii")}}

        bot = Bot(_token(), transport=transport, base_url="https://example.invalid")
        fetched = await bot.get_attachment("bot_test_media_01", {"kind": "private", "id": "chat_test_media_01"}, file_id)
        self.assertEqual(fetched["data"], data)
        self.assertEqual({key: value for key, value in fetched.items() if key != "data"}, descriptor)

    async def test_fetch_rejects_other_file_id_even_with_valid_descriptor_and_hash(self):
        data = _png()
        requested_id = "media_" + secrets.token_hex(16)
        returned_id = "media_" + secrets.token_hex(16)
        descriptor = {"fileId": returned_id, "kind": "photo", "fileName": "receipt.png",
                      "mimeType": "image/png", "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "width": 2, "height": 3}

        async def transport(method, path, headers, body, timeout):
            return {"ok": True, "result": {**descriptor, "data": base64.b64encode(data).decode("ascii")}}

        bot = Bot(_token(), transport=transport, base_url="https://example.invalid")
        with self.assertRaises(CloudBotTransportError):
            await bot.get_attachment(
                "bot_test_media_01", {"kind": "private", "id": "chat_test_media_01"}, requested_id)

    async def test_fetch_rejects_bytes_that_disagree_with_declared_photo_or_pdf_type(self):
        chat = {"kind": "private", "id": "chat_test_media_01"}
        pdf = b"%PDF-1.7\nsynthetic\n%%EOF"
        png = _png()

        cases = (
            ({"fileId": "media_" + secrets.token_hex(16), "kind": "photo", "fileName": "image.png",
              "mimeType": "image/png", "size": len(pdf), "sha256": hashlib.sha256(pdf).hexdigest(),
              "width": 2, "height": 3}, pdf),
            ({"fileId": "media_" + secrets.token_hex(16), "kind": "document", "fileName": "receipt.pdf",
              "mimeType": "application/pdf", "size": len(png), "sha256": hashlib.sha256(png).hexdigest()}, png),
        )
        for descriptor, data in cases:
            async def transport(method, path, headers, body, timeout):
                return {"ok": True, "result": {**descriptor, "data": base64.b64encode(data).decode("ascii")}}

            bot = Bot(_token(), transport=transport, base_url="https://example.invalid")
            with self.assertRaises(CloudBotTransportError):
                await bot.get_attachment("bot_test_media_01", chat, descriptor["fileId"])

    async def test_method_objects_and_bad_inputs_fail_before_transport(self):
        called = False

        async def transport(*args):
            nonlocal called
            called = True
            return {"ok": True, "result": {}}

        bot = Bot(_token(), transport=transport, base_url="https://example.invalid")
        chat = {"kind": "private", "id": "chat_test_media_01"}
        with self.assertRaises(CloudBotValidationError):
            await bot.send_photo("bot_test_media_01", chat, b"<svg>", request_id="r-1", file_name="x.svg")
        with self.assertRaises(CloudBotValidationError):
            await bot.send_document("bot_test_media_01", chat, b"%PDF-1.7", request_id="r-1", file_name="x.pdf")
        with self.assertRaises(CloudBotValidationError):
            await bot.send_document("bot_test_media_01", chat, b"%PDF-1.7\n%%EOF", request_id="r-1", file_name="../x.pdf")
        self.assertFalse(called)
        self.assertEqual(SendPhoto.__api_method__, "sendPhoto")
        self.assertEqual(SendDocument.__api_method__, "sendDocument")
        self.assertEqual(GetAttachment.__api_method__, "getAttachment")

    async def test_document_and_include_attachment_polling_use_negotiated_contract(self):
        pdf = b"%PDF-1.7\nsynthetic\n%%EOF"
        seen = []
        descriptor = {"fileId": "media_" + secrets.token_hex(16), "kind": "document",
                      "fileName": "receipt.pdf", "mimeType": "application/pdf", "size": len(pdf),
                      "sha256": hashlib.sha256(pdf).hexdigest()}

        async def transport(method, path, headers, body, timeout):
            payload = json.loads(body)
            seen.append((path, payload))
            if path.endswith("botSendAttachment"):
                return {"ok": True, "result": {"version": 1, "messageId": "5", "botId": "bot_test_media_01",
                    "chat": {"kind": "private", "id": "chat_test_media_01"}, "text": "", "attachment": descriptor}}
            return {"ok": True, "result": []}

        bot = Bot(_token(), transport=transport, base_url="https://example.invalid")
        chat = {"kind": "private", "id": "chat_test_media_01"}
        result = await SendDocument("bot_test_media_01", chat, pdf, "doc-1", "receipt.pdf").execute(bot)
        self.assertEqual(result["attachment"], descriptor)
        await bot.get_updates("bot_test_media_01", include_attachments=True)
        self.assertTrue(seen[0][1]["data"])
        self.assertTrue(seen[1][1]["includeAttachments"])

    def test_strict_dispatcher_accepts_media_only_with_exact_descriptor(self):
        descriptor = {"fileId": "media_" + secrets.token_hex(16), "kind": "document",
                      "fileName": "receipt.pdf", "mimeType": "application/pdf", "size": 12,
                      "sha256": "a" * 64}
        update = {"version": 1, "updateId": "3", "botId": "bot_test_media_01",
                  "chat": {"kind": "private", "id": "chat_test_media_01"},
                  "text": "", "attachment": descriptor}
        self.assertEqual(_update(update, "bot_test_media_01")["attachment"], descriptor)
        for malformed in ({**descriptor, "url": "https://example.invalid"},
                          {**descriptor, "size": True}, {**descriptor, "fileId": "../x"}):
            with self.assertRaises(CloudBotValidationError):
                _update({**update, "attachment": malformed}, "bot_test_media_01")


if __name__ == "__main__":
    unittest.main()

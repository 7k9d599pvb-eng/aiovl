"""Small async client for the VLook cloud-bot contract.

Private text, bounded polling, keyboards, callbacks and JPEG/PNG/PDF media
are implemented. Credentials are sent only in the authorization header.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import urllib.error
import urllib.request
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

MAX_TEXT = 4096
MAX_UPDATES = 100
MAX_TIMEOUT_SECONDS = 50
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BODY_BYTES = 14 * 1024 * 1024
_BOT = re.compile(r"^bot_[a-z0-9][a-z0-9_-]{2,63}$")
_CHAT = re.compile(r"^chat_[a-z0-9][a-z0-9_-]{2,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UPDATE = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_TOKEN_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CALLBACK_ID = re.compile(r"^callback_[0-9a-f]{32}$")
_FILE_ID = re.compile(r"^media_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PHOTO_MIMES = {"image/jpeg", "image/png"}


class CloudBotError(Exception):
    """Base error with no credential or provider detail in its message."""


class CloudBotValidationError(CloudBotError):
    pass


class CloudBotTransportError(CloudBotError):
    def __init__(self, message: str = "Cloud bot transport failed.", *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable is True


class AsyncTransport(Protocol):
    async def __call__(self, method: str, path: str, headers: Mapping[str, str], body: bytes, timeout: float) -> Mapping[str, Any]: ...


def _fail(message: str = "Invalid cloud bot request.") -> None:
    raise CloudBotValidationError(message)


def _bot(value: Any) -> str:
    if not isinstance(value, str) or not _BOT.fullmatch(value):
        _fail("Invalid cloud bot id.")
    return value


def _request_timeout(value: Any) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not 0 < value <= 120):
        _fail("Invalid request timeout.")
    return float(value)


def _private_arguments(bot_id: Any, chat: Any, value: Any) -> tuple[Any, Any, Any]:
    """Accept both (chat, value) and the legacy (bot_id, chat, value)."""
    if isinstance(bot_id, Mapping):
        if chat is not None and value is not None:
            _fail("Ambiguous cloud bot arguments.")
        return None, bot_id, chat if chat is not None else value
    if bot_id is not None:
        _bot(bot_id)
    return bot_id, chat, value


def _chat(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "id"} or value.get("kind") != "private":
        _fail("Only private cloud bot chats are supported.")
    chat_id = value.get("id")
    if not isinstance(chat_id, str) or not _CHAT.fullmatch(chat_id):
        _fail("Invalid private cloud bot chat.")
    return {"kind": "private", "id": chat_id}


def _text(value: Any) -> str:
    # Newline and tab are valid message content; other controls are rejected.
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT or _TOKEN_CONTROL.search(value.replace("\n", "").replace("\t", "")):
        _fail("Invalid cloud bot text.")
    return value


def _button_text(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or any(unicodedata.category(ch) == "Cc" for ch in value):
        _fail("Invalid cloud bot button text.")
    return value


def _callback_data(value: Any) -> str:
    if not isinstance(value, str) or any(unicodedata.category(ch) == "Cc" for ch in value):
        _fail("Invalid cloud bot callback data.")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _fail("Invalid cloud bot callback data.")
    if not 1 <= len(encoded) <= 64:
        _fail("Invalid cloud bot callback data.")
    return value


def _reply_markup(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or type(value.get("type")) is not str:
        _fail("Invalid cloud bot reply markup.")
    kind = value["type"]
    if kind == "remove":
        if set(value) != {"type"}:
            _fail("Invalid cloud bot reply markup.")
        normalized: dict[str, Any] = {"type": "remove"}
    elif kind in ("inline", "reply"):
        if set(value) != {"type", "rows"}:
            _fail("Invalid cloud bot reply markup.")
        rows = value["rows"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
            _fail("Invalid cloud bot reply markup.")
        total = 0
        normalized_rows: list[list[dict[str, str]]] = []
        for row in rows:
            if not isinstance(row, list) or not 1 <= len(row) <= 8:
                _fail("Invalid cloud bot reply markup.")
            total += len(row)
            if total > 48:
                _fail("Invalid cloud bot reply markup.")
            normalized_row = []
            for button in row:
                if not isinstance(button, Mapping):
                    _fail("Invalid cloud bot reply markup.")
                if kind == "inline":
                    if set(button) != {"text", "callbackData"}:
                        _fail("Invalid cloud bot reply markup.")
                    normalized_row.append({"text": _button_text(button["text"]),
                                           "callbackData": _callback_data(button["callbackData"])})
                else:
                    if set(button) != {"text"}:
                        _fail("Invalid cloud bot reply markup.")
                    normalized_row.append({"text": _button_text(button["text"])})
            normalized_rows.append(normalized_row)
        normalized = {"type": kind, "rows": normalized_rows}
    else:
        _fail("Invalid cloud bot reply markup.")
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "strict")
    except (UnicodeEncodeError, TypeError, ValueError):
        _fail("Invalid cloud bot reply markup.")
    if len(encoded) > 8192:
        _fail("Invalid cloud bot reply markup.")
    return normalized


def _callback_query_id(value: Any) -> str:
    if not isinstance(value, str) or not _CALLBACK_ID.fullmatch(value):
        _fail("Invalid cloud bot callback query id.")
    return value


def _update_id(value: Any) -> str:
    if not isinstance(value, str) or not _UPDATE.fullmatch(value) or int(value) > 9223372036854775807:
        _fail("Invalid cloud bot update offset.")
    return value


def _response(value: Mapping[str, Any]) -> Any:
    if set(value) - {"ok", "result", "description"} or value.get("ok") is not True or "result" not in value:
        raise CloudBotTransportError("Cloud bot service returned an invalid response.")
    return value["result"]


def _attachment_name(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 128 or value != value.strip()
            or value in (".", "..") or "/" in value or "\\" in value
            or any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value)):
        _fail("Invalid cloud bot attachment name.")
    try:
        if len(value.encode("utf-8", "strict")) > 255:
            _fail("Invalid cloud bot attachment name.")
    except UnicodeEncodeError:
        _fail("Invalid cloud bot attachment name.")
    return value


def _attachment_bytes(source: Any) -> tuple[bytes, str | None]:
    name = None
    if isinstance(source, bytes):
        data = source
    elif isinstance(source, (bytearray, memoryview)):
        data = bytes(source)
    elif isinstance(source, (str, Path)):
        path = Path(source)
        name = path.name
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        except OSError:
            _fail("Cloud bot attachment could not be read.")
    else:
        _fail("Cloud bot attachment must be bytes or a file path.")
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_ATTACHMENT_BYTES:
        _fail("Cloud bot attachment exceeds the supported size.")
    return data, name


def _attachment_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"fileId", "kind", "fileName", "mimeType", "size", "sha256", *(('width', 'height') if value.get('kind') == 'photo' else ())}:
        _fail("Cloud bot attachment response is invalid.")
    if not isinstance(value.get("fileId"), str) or not _FILE_ID.fullmatch(value["fileId"]):
        _fail("Cloud bot attachment response is invalid.")
    kind = value["kind"]
    name = _attachment_name(value["fileName"])
    mimes = _PHOTO_MIMES if kind == "photo" else ({"application/pdf"} if kind == "document" else set())
    if (value["mimeType"] not in mimes or type(value["size"]) is not int
            or not 1 <= value["size"] <= MAX_ATTACHMENT_BYTES
            or not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"])):
        _fail("Cloud bot attachment response is invalid.")
    result = {key: value[key] for key in value}
    result["fileName"] = name
    if kind == "photo":
        width, height = value["width"], value["height"]
        if (type(width) is not int or type(height) is not int or width < 1 or height < 1
                or width * height > 64_000_000):
            _fail("Cloud bot attachment response is invalid.")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward bot credentials to another endpoint.
        raise CloudBotTransportError("Cloud bot redirects are not allowed.")


async def _https_transport(base_url: str, token: str, method: str, path: str, headers: Mapping[str, str], body: bytes, timeout: float, max_response_bytes: int) -> Mapping[str, Any]:
    def request() -> Mapping[str, Any]:
        url = base_url.rstrip("/") + path
        request_obj = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request_obj, timeout=timeout) as response:
                raw = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (429, 502, 503, 504)
            exc.close()
            raise CloudBotTransportError("Cloud bot HTTP request failed.", retryable=retryable) from None
        except (OSError, urllib.error.URLError, TimeoutError):
            raise CloudBotTransportError("Cloud bot transport failed.", retryable=True) from None
        if len(raw) > max_response_bytes:
            raise CloudBotTransportError("Cloud bot response exceeded the limit.")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CloudBotTransportError("Cloud bot service returned invalid JSON.") from None
        if not isinstance(value, Mapping):
            raise CloudBotTransportError("Cloud bot service returned an invalid response.")
        return value

    return await asyncio.to_thread(request)


class Bot:
    """Experimental VLook cloud-bot client, intentionally not aiogram-compatible."""

    def __init__(self, token: str, *, transport: AsyncTransport | None = None, base_url: str = "https://89.124.86.75") -> None:
        if not isinstance(token, str) or len(token) < 16 or len(token) > 512 or not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
            _fail("Invalid cloud bot token.")
        try:
            endpoint = urlsplit(base_url)
            valid = (isinstance(base_url, str) and endpoint.scheme == "https" and endpoint.hostname
                     and endpoint.username is None and endpoint.password is None
                     and not endpoint.query and not endpoint.fragment and endpoint.path in ("", "/")
                     and not any(ord(c) <= 32 or ord(c) == 127 for c in base_url))
            endpoint.port  # Validate malformed ports before any network request.
        except (TypeError, ValueError):
            valid = False
        if not valid:
            _fail("Invalid cloud bot endpoint.")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._resolved_bot_id: str | None = None
        self._identity_task: asyncio.Task[dict[str, Any]] | None = None
        self._active_callback_query_id: str | None = None
        self._active_callback_answered = False

    def __repr__(self) -> str:
        return "Bot(token=<redacted>, transport=<configured>)"

    async def _call(self, path: str, payload: Mapping[str, Any], timeout: float) -> Any:
        timeout = _request_timeout(timeout)
        try:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "strict")
        except (UnicodeEncodeError, TypeError, ValueError):
            _fail("Invalid cloud bot request.")
        media_request = path.endswith(("SendAttachment", "GetAttachment"))
        max_request_bytes = MAX_MEDIA_BODY_BYTES if media_request else 64 * 1024
        max_response_bytes = MAX_MEDIA_BODY_BYTES if path.endswith("GetAttachment") else 1024 * 1024
        if len(body) > max_request_bytes:
            _fail("Cloud bot request exceeds the supported size.")
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            if self._transport is None:
                result = await _https_transport(self._base_url, self._token, "POST", path, headers, body, float(timeout), max_response_bytes)
            else:
                result = await asyncio.wait_for(self._transport("POST", path, headers, body, float(timeout)), timeout=float(timeout))
        except CloudBotTransportError as exc:
            # Injected transports must not expose provider bodies or secrets.
            raise CloudBotTransportError("Cloud bot transport failed.", retryable=exc.retryable) from None
        except CloudBotError:
            raise CloudBotTransportError("Cloud bot transport failed.") from None
        except asyncio.TimeoutError:
            raise CloudBotTransportError("Cloud bot request timed out.", retryable=True) from None
        except OSError:
            raise CloudBotTransportError("Cloud bot transport failed.", retryable=True) from None
        except Exception:
            raise CloudBotTransportError("Cloud bot transport failed.") from None
        if not isinstance(result, Mapping):
            raise CloudBotTransportError("Cloud bot service returned an invalid response.")
        try:
            if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "strict")) > max_response_bytes:
                raise CloudBotTransportError("Cloud bot response exceeded the limit.")
        except (UnicodeEncodeError, TypeError, ValueError):
            raise CloudBotTransportError("Cloud bot service returned an invalid response.") from None
        return _response(result)

    async def _fetch_identity(self, timeout: float) -> dict[str, Any]:
        result = await self._call("/v1/cloud-bots/getMe", {
            "version": 1, "operation": "getMe",
        }, timeout)
        if (not isinstance(result, Mapping) or set(result) != {"version", "botId"}
                or type(result.get("version")) is not int or result["version"] != 1
                or not isinstance(result.get("botId"), str) or not _BOT.fullmatch(result["botId"])):
            raise CloudBotTransportError("Cloud bot identity response is invalid.")
        bot_id = result["botId"]
        if self._resolved_bot_id is not None and self._resolved_bot_id != bot_id:
            raise CloudBotTransportError("Cloud bot identity did not match.")
        self._resolved_bot_id = bot_id
        return {"version": 1, "botId": bot_id}

    def _identity_finished(self, task: asyncio.Task[dict[str, Any]]) -> None:
        if self._identity_task is task:
            self._identity_task = None
        # One cancelled waiter does not cancel shared read-only discovery.
        # Consume errors even when every waiter has already gone away.
        if not task.cancelled():
            task.exception()

    async def get_me(self, *, timeout: float = 30) -> dict[str, Any]:
        """Authenticate the token and return its public ID; share overlapping calls."""
        timeout = _request_timeout(timeout)
        task = self._identity_task
        if task is None or task.done():
            task = asyncio.create_task(self._fetch_identity(timeout))
            self._identity_task = task
            task.add_done_callback(self._identity_finished)
        try:
            return dict(await asyncio.wait_for(asyncio.shield(task), timeout))
        except asyncio.TimeoutError:
            raise CloudBotTransportError("Cloud bot request timed out.", retryable=True) from None

    async def _resolve_bot_id(self, bot_id: str | None = None, *, timeout: float = 30) -> str:
        _request_timeout(timeout)
        if bot_id is not None:
            bot_id = _bot(bot_id)
            if self._resolved_bot_id is not None and self._resolved_bot_id != bot_id:
                _fail("Cloud bot id did not match the authenticated identity.")
            return bot_id
        if self._resolved_bot_id is None:
            return (await self.get_me(timeout=timeout))["botId"]
        # The cache contains only a public ID, never an authorization decision.
        # Each subsequent operation authenticates with the same bearer token.
        return self._resolved_bot_id

    async def send_message(self, bot_id: str | Mapping[str, str] | None = None, chat: Mapping[str, str] | str | None = None, text: str | None = None, *, request_id: str, reply_markup: Mapping[str, Any] | None = None, timeout: float = 30) -> Any:
        bot_id, chat, text = _private_arguments(bot_id, chat, text)
        chat = _chat(chat)
        text = _text(text)
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            _fail("Invalid cloud bot request id.")
        normalized_markup = _reply_markup(reply_markup) if reply_markup is not None else None
        bot_id = await self._resolve_bot_id(bot_id, timeout=timeout)
        payload: dict[str, Any] = {"version": 1, "operation": "sendText", "botId": bot_id, "chat": chat, "text": text, "requestId": request_id}
        if normalized_markup is not None:
            payload["replyMarkup"] = normalized_markup
        result = await self._call("/v1/cloud-bots/sendText", payload, timeout)
        if isinstance(result, Mapping) and "botId" in result and result["botId"] != bot_id:
            raise CloudBotTransportError("Cloud bot send response did not match the authenticated identity.")
        if isinstance(result, Mapping) and "replyMarkup" in result:
            response = dict(result)
            try:
                response["replyMarkup"] = _reply_markup(response["replyMarkup"])
            except CloudBotValidationError:
                raise CloudBotTransportError("Cloud bot reply markup response is invalid.") from None
            if normalized_markup is not None and response["replyMarkup"] != normalized_markup:
                raise CloudBotTransportError("Cloud bot reply markup response did not match the request.")
            return response
        return result

    @staticmethod
    def _caption(value: Any) -> str:
        if not isinstance(value, str):
            _fail("Invalid cloud bot attachment caption.")
        try:
            if len(value.encode("utf-8", "strict")) > 1024:
                _fail("Invalid cloud bot attachment caption.")
        except UnicodeEncodeError:
            _fail("Invalid cloud bot attachment caption.")
        if any((ord(ch) < 0x20 and ch not in "\n\t") or 0x7f <= ord(ch) <= 0x9f for ch in value):
            _fail("Invalid cloud bot attachment caption.")
        return value

    async def _send_attachment(self, operation: str, bot_id: str | None, chat: Mapping[str, str],
                               source: Any, *, kind: str, file_name: str | None,
                               caption: str, request_id: str, timeout: float) -> dict[str, Any]:
        chat = _chat(chat)
        data, inferred_name = _attachment_bytes(source)
        file_name = _attachment_name(file_name if file_name is not None else inferred_name)
        caption = self._caption(caption)
        if kind == "photo":
            if data.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            else:
                _fail("Only JPEG and PNG photos are supported.")
        else:
            if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
                _fail("Only PDF documents are supported.")
            mime = "application/pdf"
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            _fail("Invalid cloud bot request id.")
        bot_id = await self._resolve_bot_id(bot_id, timeout=timeout)
        payload = {"version": 1, "operation": operation, "botId": bot_id, "chat": chat,
                   "kind": kind, "fileName": file_name, "mimeType": mime, "caption": caption,
                   "data": base64.b64encode(data).decode("ascii"), "requestId": request_id}
        result = await self._call(f"/v1/cloud-bots/{operation}", payload, timeout)
        identifier = "messageId" if operation == "botSendAttachment" else "updateId"
        if (not isinstance(result, Mapping) or set(result) != {"version", identifier, "botId", "chat", "text", "attachment"}
                or type(result.get("version")) is not int or result["version"] != 1
                or result.get(identifier) is None or not isinstance(result[identifier], str)
                or not _UPDATE.fullmatch(result[identifier]) or int(result[identifier]) > 9223372036854775807
                or result.get("botId") != bot_id or result.get("chat") != chat
                or result.get("text") != caption):
            raise CloudBotTransportError("Cloud bot attachment response is invalid.")
        try:
            descriptor = _attachment_descriptor(result["attachment"])
        except CloudBotValidationError:
            raise CloudBotTransportError("Cloud bot attachment response is invalid.") from None
        if descriptor["kind"] != kind or descriptor["fileName"] != file_name or descriptor["mimeType"] != mime or descriptor["size"] != len(data) or descriptor["sha256"] != hashlib.sha256(data).hexdigest():
            raise CloudBotTransportError("Cloud bot attachment response did not match the request.")
        return dict(result)

    async def send_photo(self, bot_id: str | Mapping[str, str] | None = None, chat: Any = None, photo: Any = None, *,
                         request_id: str, file_name: str | None = None,
                         caption: str = "", timeout: float = 60) -> dict[str, Any]:
        """Send unchanged, locally prepared JPEG/PNG bytes or a bounded local path."""
        bot_id, chat, photo = _private_arguments(bot_id, chat, photo)
        return await self._send_attachment("botSendAttachment", bot_id, chat, photo,
                                           kind="photo", file_name=file_name,
                                           caption=caption, request_id=request_id, timeout=timeout)

    async def send_document(self, bot_id: str | Mapping[str, str] | None = None, chat: Any = None, document: Any = None, *,
                            request_id: str, file_name: str | None = None,
                            caption: str = "", timeout: float = 60) -> dict[str, Any]:
        """Send a bounded PDF document; arbitrary executable/web formats are not accepted."""
        bot_id, chat, document = _private_arguments(bot_id, chat, document)
        return await self._send_attachment("botSendAttachment", bot_id, chat, document,
                                           kind="document", file_name=file_name,
                                           caption=caption, request_id=request_id, timeout=timeout)

    async def get_attachment(self, bot_id: str | Mapping[str, str] | None = None, chat: Mapping[str, str] | str | None = None, file_id: str | None = None, *,
                             timeout: float = 60) -> dict[str, Any]:
        bot_id, chat, file_id = _private_arguments(bot_id, chat, file_id)
        chat = _chat(chat)
        if not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
            _fail("Invalid cloud bot file id.")
        bot_id = await self._resolve_bot_id(bot_id, timeout=timeout)
        result = await self._call("/v1/cloud-bots/botGetAttachment", {
            "version": 1, "operation": "botGetAttachment", "botId": bot_id,
            "chat": chat, "fileId": file_id,
        }, timeout)
        if not isinstance(result, Mapping) or set(result) != {"fileId", "kind", "fileName", "mimeType", "size", "sha256", "data", *(('width', 'height') if result.get('kind') == 'photo' else ())}:
            raise CloudBotTransportError("Cloud bot attachment response is invalid.")
        try:
            descriptor = _attachment_descriptor({key: value for key, value in result.items() if key != "data"})
            if descriptor["fileId"] != file_id:
                raise CloudBotValidationError("Attachment identifier did not match the request.")
            encoded = result["data"]
            if not isinstance(encoded, str) or len(encoded) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4:
                raise CloudBotValidationError("Invalid attachment data.")
            data = base64.b64decode(encoded.encode("ascii"), validate=True)
            if (len(data) != descriptor["size"] or hashlib.sha256(data).hexdigest() != descriptor["sha256"]):
                raise CloudBotValidationError("Invalid attachment data.")
            if descriptor["kind"] == "photo":
                if descriptor["mimeType"] == "image/jpeg":
                    valid_signature = data.startswith(b"\xff\xd8\xff")
                else:
                    valid_signature = data.startswith(b"\x89PNG\r\n\x1a\n")
            else:
                valid_signature = data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]
            if not valid_signature:
                raise CloudBotValidationError("Attachment bytes did not match the declared type.")
        except (CloudBotValidationError, ValueError, UnicodeError):
            raise CloudBotTransportError("Cloud bot attachment response is invalid.") from None
        return {**descriptor, "data": data}

    async def answer_callback_query(self, bot_id: str | None = None, callback_query_id: str | None = None, *, request_id: str, text: str = "", show_alert: bool = False, timeout: float = 30) -> dict[str, Any]:
        if callback_query_id is None and bot_id is not None:
            bot_id, callback_query_id = None, bot_id
        callback_query_id = _callback_query_id(callback_query_id)
        if (not isinstance(text, str) or len(text) > 200 or
                any(unicodedata.category(ch) == "Cc" for ch in text)):
            _fail("Invalid cloud bot callback answer text.")
        if type(show_alert) is not bool:
            _fail("Invalid cloud bot callback alert flag.")
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            _fail("Invalid cloud bot request id.")
        bot_id = await self._resolve_bot_id(bot_id, timeout=timeout)
        payload: dict[str, Any] = {
            "version": 1, "operation": "answerCallbackQuery", "botId": bot_id,
            "callbackQueryId": callback_query_id, "requestId": request_id,
        }
        if text:
            payload["text"] = text
        if show_alert:
            payload["showAlert"] = True
        result = await self._call(
            "/v1/cloud-bots/answerCallbackQuery",
            payload, timeout)
        if (not isinstance(result, Mapping) or set(result) != {"answered", "callbackQueryId"} or
                result.get("answered") is not True or result.get("callbackQueryId") != callback_query_id):
            raise CloudBotTransportError("Cloud bot callback answer response is invalid.")
        if self._active_callback_query_id == callback_query_id:
            self._active_callback_answered = True
        return {"answered": True, "callbackQueryId": callback_query_id}

    def _begin_callback_dispatch(self, callback_query_id: str) -> None:
        self._active_callback_query_id = callback_query_id
        self._active_callback_answered = False

    def _finish_callback_dispatch(self, callback_query_id: str) -> bool:
        answered = self._active_callback_query_id == callback_query_id and self._active_callback_answered
        if self._active_callback_query_id == callback_query_id:
            self._active_callback_query_id = None
            self._active_callback_answered = False
        return answered

    async def get_updates(self, bot_id: str | None = None, *, offset: str | None = None, limit: int = 100, timeout: int = 0, request_timeout: float = 60, include_attachments: bool = False) -> Any:
        if offset is not None:
            _update_id(offset)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_UPDATES:
            _fail("Invalid cloud bot update limit.")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 0 <= timeout <= MAX_TIMEOUT_SECONDS:
            _fail("Invalid cloud bot polling timeout.")
        if type(include_attachments) is not bool:
            _fail("Invalid attachment capability.")
        bot_id = await self._resolve_bot_id(bot_id, timeout=request_timeout)
        payload: dict[str, Any] = {"version": 1, "operation": "getUpdates", "botId": bot_id, "limit": limit, "timeoutSeconds": timeout}
        if offset is not None:
            payload["offset"] = offset
        if include_attachments:
            payload["includeAttachments"] = True
        return await self._call("/v1/cloud-bots/getUpdates", payload, request_timeout)

    async def acknowledge_updates(self, bot_id: str | None = None, through: str | None = None) -> str:
        """Acknowledge the completed prefix through one update ID.

        This is separate from ``get_updates``: polling remains read-only and
        ``offset`` continues to mean the exclusive lower bound ``id > offset``.
        The fixed timeout is bounded; callers may safely retry the same cursor.
        """
        if through is None and bot_id is not None:
            bot_id, through = None, bot_id
        through = _update_id(through)
        bot_id = await self._resolve_bot_id(bot_id)
        result = await self._call(
            "/v1/cloud-bots/acknowledgeUpdates",
            {"version": 1, "operation": "acknowledgeUpdates", "botId": bot_id,
             "through": through},
            30,
        )
        if not isinstance(result, Mapping) or set(result) != {"acknowledgedThrough"}:
            raise CloudBotTransportError("Cloud bot acknowledgement response is invalid.")
        acknowledged = result["acknowledgedThrough"]
        if not isinstance(acknowledged, str) or not _UPDATE.fullmatch(acknowledged) or int(acknowledged) > 9223372036854775807:
            raise CloudBotTransportError("Cloud bot acknowledgement response is invalid.")
        return acknowledged

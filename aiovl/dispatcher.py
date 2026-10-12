"""Small adapted ordered polling dispatcher for the VLook cloud-bot subset.

Copyright (c) 2017 - present Alex Root Junior
This file is an AIOVL adaptation of aiogram's handler/dispatcher shape.
The MIT notice is included with the distribution; provenance is in ``UPSTREAM.json``.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .client import Bot, CloudBotTransportError, CloudBotValidationError, _attachment_descriptor, _bot

Handler = Callable[[Mapping[str, Any]], Any]
_TRANSPORT_RETRY_DELAYS = (2.0, 4.0, 8.0, 16.0, 30.0)
_UPDATE_ID = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_BOT_ID = re.compile(r"^bot_[a-z0-9][a-z0-9_-]{2,63}$")
_CHAT_ID = re.compile(r"^chat_[a-z0-9][a-z0-9_-]{2,63}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CALLBACK_ID = re.compile(r"^callback_[0-9a-f]{32}$")
_POSITIVE_INT = re.compile(r"^[1-9][0-9]{0,18}$")


def _callback_data(value: Any) -> str:
    if not isinstance(value, str) or any(unicodedata.category(ch) == "Cc" for ch in value):
        raise CloudBotValidationError("Invalid cloud bot callback data.")
    try:
        length = len(value.encode("utf-8", "strict"))
    except UnicodeEncodeError:
        raise CloudBotValidationError("Invalid cloud bot callback data.") from None
    if not 1 <= length <= 64:
        raise CloudBotValidationError("Invalid cloud bot callback data.")
    return value


def _private_chat(value: Any) -> dict[str, str]:
    if (not isinstance(value, Mapping) or set(value) != {"kind", "id"} or
            value.get("kind") != "private" or not isinstance(value.get("id"), str) or
            not _CHAT_ID.fullmatch(value["id"])):
        raise CloudBotValidationError("Invalid cloud bot update.")
    return {"kind": "private", "id": value["id"]}


def _update(value: Any, expected_bot_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CloudBotValidationError("Invalid cloud bot update.")
    keys = set(value)
    legacy_keys = {"version", "updateId", "botId", "chat", "text"}
    attachment_keys = legacy_keys | {"attachment"}
    callback_keys = {"version", "updateId", "botId", "chat", "callbackQuery"}
    if keys not in (legacy_keys, attachment_keys, callback_keys):
        raise CloudBotValidationError("Invalid cloud bot update.")
    if isinstance(value["version"], bool) or not isinstance(value["version"], int) or value["version"] != 1 or value["botId"] != expected_bot_id:
        raise CloudBotValidationError("Invalid cloud bot update.")
    update_id = value["updateId"]
    if not isinstance(update_id, str) or not _UPDATE_ID.fullmatch(update_id) or int(update_id) > 9223372036854775807:
        raise CloudBotValidationError("Invalid cloud bot update.")
    chat = _private_chat(value["chat"])
    if keys in (legacy_keys, attachment_keys):
        text = value["text"]
        if keys == legacy_keys and (not isinstance(text, str) or not text or len(text) > 4096 or _CONTROL.search(text)):
            raise CloudBotValidationError("Invalid cloud bot update.")
        normalized = {"version": 1, "updateId": update_id, "botId": expected_bot_id,
                      "chat": chat, "text": text}
        if keys == attachment_keys:
            if not isinstance(text, str) or len(text.encode("utf-8", "strict")) > 1024 or any(
                    (ord(ch) < 0x20 and ch not in "\n\t") or 0x7f <= ord(ch) <= 0x9f for ch in text):
                raise CloudBotValidationError("Invalid cloud bot update.")
            try:
                normalized["attachment"] = _attachment_descriptor(value["attachment"])
            except CloudBotValidationError:
                raise CloudBotValidationError("Invalid cloud bot update.") from None
        return normalized
    query = value["callbackQuery"]
    if not isinstance(query, Mapping) or set(query) != {"id", "messageId", "data"}:
        raise CloudBotValidationError("Invalid cloud bot update.")
    query_id, message_id = query.get("id"), query.get("messageId")
    if not isinstance(query_id, str) or not _CALLBACK_ID.fullmatch(query_id):
        raise CloudBotValidationError("Invalid cloud bot update.")
    if (not isinstance(message_id, str) or not _POSITIVE_INT.fullmatch(message_id) or
            int(message_id) > 9223372036854775807):
        raise CloudBotValidationError("Invalid cloud bot update.")
    data = _callback_data(query.get("data"))
    return {"version": 1, "updateId": update_id, "botId": expected_bot_id,
            "chat": chat, "callbackQuery": {"id": query_id, "messageId": message_id, "data": data}}


@dataclass(frozen=True, slots=True)
class _Handler:
    kind: str
    value: str | None
    callback: Handler


class Router:
    """Ordered first-match text/command handlers for private cloud updates."""

    def __init__(self) -> None:
        self._handlers: list[_Handler] = []

    def on_text(self, callback: Handler | None = None):
        def register(handler: Handler) -> Handler:
            self._handlers.append(_Handler("text", None, handler))
            return handler
        return register(callback) if callback is not None else register

    def on_command(self, command: str):
        if not isinstance(command, str) or not command.startswith("/") or len(command) < 2 or any(ch.isspace() for ch in command):
            raise ValueError("Invalid cloud bot command.")
        def register(handler: Handler) -> Handler:
            self._handlers.append(_Handler("command", command, handler))
            return handler
        return register

    def on_callback(self, callback_data: str | None = None):
        """Register an exact callback-data match, or all callback queries when omitted."""
        if callback_data is not None:
            callback_data = _callback_data(callback_data)
        def register(handler: Handler) -> Handler:
            self._handlers.append(_Handler("callback", callback_data, handler))
            return handler
        return register

    def on_attachment(self, callback: Handler | None = None):
        """Register for either a photo or document attachment update."""
        def register(handler: Handler) -> Handler:
            self._handlers.append(_Handler("attachment", None, handler))
            return handler
        return register(callback) if callback is not None else register

    def on_photo(self, callback: Handler | None = None):
        def register(handler: Handler) -> Handler:
            self._handlers.append(_Handler("photo", None, handler))
            return handler
        return register(callback) if callback is not None else register

    def on_document(self, callback: Handler | None = None):
        def register(handler: Handler) -> Handler:
            self._handlers.append(_Handler("document", None, handler))
            return handler
        return register(callback) if callback is not None else register

    async def dispatch(self, update: Mapping[str, Any]) -> bool:
        query = update.get("callbackQuery")
        if isinstance(query, Mapping):
            data = query["data"]
            for registered in self._handlers:
                if registered.kind != "callback" or (registered.value is not None and registered.value != data):
                    continue
                result = registered.callback(update)
                if inspect.isawaitable(result):
                    await result
                return True
            return False
        attachment = update.get("attachment")
        if isinstance(attachment, Mapping):
            kind = attachment["kind"]
            for registered in self._handlers:
                if registered.kind not in ("attachment", kind):
                    continue
                result = registered.callback(update)
                if inspect.isawaitable(result):
                    await result
                return True
            if not update["text"]:
                return False
        text = update["text"]
        for registered in self._handlers:
            words = text.split(maxsplit=1)
            matches = registered.kind == "text" or (bool(words) and words[0] == registered.value)
            if not matches:
                continue
            result = registered.callback(update)
            if inspect.isawaitable(result):
                await result
            return True
        return False


class Dispatcher:
    """Poll updates in order; optionally acknowledge each handled prefix."""

    def __init__(self, bot: Bot, bot_id: str | Router | None = None, router: Router | None = None, *, limit: int = 100, timeout: int = 0, acknowledge: bool = False, retry_transport: bool = False, include_attachments: bool = False) -> None:
        if isinstance(bot_id, Router):
            if router is not None:
                raise CloudBotValidationError("Ambiguous cloud bot dispatcher arguments.")
            bot_id, router = None, bot_id
        self.bot = bot
        self.bot_id = _bot(bot_id) if bot_id is not None else None
        self.router = router or Router()
        self.limit = limit
        self.timeout = timeout
        if not isinstance(acknowledge, bool):
            raise ValueError("acknowledge must be a boolean")
        if not isinstance(retry_transport, bool):
            raise ValueError("retry_transport must be a boolean")
        if not isinstance(include_attachments, bool):
            raise ValueError("include_attachments must be a boolean")
        self.acknowledge = acknowledge
        self.retry_transport = retry_transport
        self.include_attachments = include_attachments
        self.offset: str | None = None
        self._pending_ack: str | None = None
        self._stop = asyncio.Event()
        self._polling_task: asyncio.Task[None] | None = None

    def stop(self) -> None:
        self._stop.set()
        task = self._polling_task
        if task is not None and not task.done():
            task.cancel()

    async def poll_once(self) -> int:
        if self.bot_id is None:
            self.bot_id = await self._transport_call(lambda: self.bot._resolve_bot_id())
        await self._flush_pending_ack()
        updates = await self._transport_call(lambda: self.bot.get_updates(
            self.bot_id, offset=self.offset, limit=self.limit, timeout=self.timeout,
            include_attachments=self.include_attachments))
        if not isinstance(updates, list) or len(updates) > self.limit:
            raise CloudBotValidationError("Cloud bot updates response is invalid.")
        validated = [_update(raw, self.bot_id) for raw in updates]
        ids = [int(item["updateId"]) for item in validated]
        if any(left >= right for left, right in zip(ids, ids[1:])):
            raise CloudBotValidationError("Cloud bot updates are out of order.")
        cursor = int(self.offset) if self.offset is not None else -1
        handled = 0
        for update, update_id in zip(validated, ids):
            if update_id <= cursor:
                continue
            query = update.get("callbackQuery")
            if isinstance(query, Mapping):
                query_id = query["id"]
                self.bot._begin_callback_dispatch(query_id)
                try:
                    await self.router.dispatch(update)
                finally:
                    answered = self.bot._finish_callback_dispatch(query_id)
                if not answered:
                    raise CloudBotValidationError("Callback query must be answered before update acknowledgement.")
            else:
                await self.router.dispatch(update)
            if self.acknowledge:
                # Track the completed contiguous prefix, but defer network I/O
                # until the batch ends. If a later handler fails, this cursor
                # remains pending and is flushed before the next poll.
                self._pending_ack = update["updateId"]
            else:
                self.offset = update["updateId"]
            cursor = update_id
            handled += 1
        # One durable state write per processed batch instead of one per item.
        # A lost response leaves this cursor pending for an ack-only retry.
        await self._flush_pending_ack()
        return handled

    async def _flush_pending_ack(self) -> None:
        if not self.acknowledge or self._pending_ack is None:
            return
        pending = self._pending_ack
        acknowledged = await self._transport_call(
            lambda: self.bot.acknowledge_updates(self.bot_id, pending))
        if acknowledged != pending:
            raise CloudBotTransportError("Cloud bot acknowledgement cursor did not match.")
        self.offset = pending
        self._pending_ack = None

    async def _transport_call(self, operation):
        """Retry only transport-classified read/ack calls, never handlers."""
        attempt = 0
        while True:
            try:
                return await operation()
            except CloudBotTransportError as exc:
                if (not self.retry_transport or getattr(exc, "retryable", False) is not True
                        or attempt >= len(_TRANSPORT_RETRY_DELAYS)):
                    raise
                delay = _TRANSPORT_RETRY_DELAYS[attempt]
                attempt += 1
                # Waiting on the stop event makes backoff interruptible even
                # when the transport task itself is not the polling task.
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    continue
                raise asyncio.CancelledError

    async def run_polling(self) -> None:
        if self._polling_task is not None:
            raise RuntimeError("Cloud bot polling is already running.")
        self._stop.clear()
        task = asyncio.current_task()
        self._polling_task = task
        try:
            while not self._stop.is_set():
                handled = await self.poll_once()
                if handled == 0 and not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            if not self._stop.is_set():
                raise
        finally:
            self._polling_task = None


__all__ = ["Dispatcher", "Router"]

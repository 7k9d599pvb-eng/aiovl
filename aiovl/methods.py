"""Small adapted method objects for the VLook cloud-bot subset.

Copyright (c) 2017 - present Alex Root Junior
This file is an AIOVL adaptation of the bounded method-object idea in aiogram.
The MIT notice is included with the distribution; provenance is in ``UPSTREAM.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .client import Bot, CloudBotValidationError


class _Executable(Protocol):
    async def execute(self, bot: Bot) -> Any: ...


def _nonempty_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise CloudBotValidationError("Invalid cloud bot text.")
    return value


@dataclass(frozen=True, slots=True)
class SendMessage:
    """Adapted ``sendMessage`` method; only the VLook private-text subset exists."""

    bot_id: str | None = None
    chat: Mapping[str, str] | None = None
    text: str | None = None
    request_id: str | None = None
    reply_markup: Mapping[str, Any] | None = None
    __api_method__ = "sendMessage"

    def __post_init__(self) -> None:
        _nonempty_text(self.text)
        if not isinstance(self.request_id, str) or not self.request_id:
            raise CloudBotValidationError("Invalid cloud bot request id.")

    async def execute(self, bot: Bot) -> Any:
        result = await bot.send_message(
            self.bot_id,
            self.chat,
            self.text,
            request_id=self.request_id,
            reply_markup=self.reply_markup,
        )
        if not isinstance(result, Mapping):
            raise CloudBotValidationError("Cloud bot send response is invalid.")
        return result


@dataclass(frozen=True, slots=True)
class SendPhoto:
    """Send one locally prepared JPEG/PNG attachment (maximum 10 MiB)."""

    bot_id: str | None = None
    chat: Mapping[str, str] | None = None
    photo: Any = None
    request_id: str | None = None
    file_name: str | None = None
    caption: str = ""
    __api_method__ = "sendPhoto"

    async def execute(self, bot: Bot) -> Any:
        return await bot.send_photo(self.bot_id, self.chat, self.photo,
                                    request_id=self.request_id, file_name=self.file_name,
                                    caption=self.caption)


@dataclass(frozen=True, slots=True)
class SendDocument:
    """Send one PDF attachment; arbitrary active-content formats are rejected."""

    bot_id: str | None = None
    chat: Mapping[str, str] | None = None
    document: Any = None
    request_id: str | None = None
    file_name: str | None = None
    caption: str = ""
    __api_method__ = "sendDocument"

    async def execute(self, bot: Bot) -> Any:
        return await bot.send_document(self.bot_id, self.chat, self.document,
                                       request_id=self.request_id, file_name=self.file_name,
                                       caption=self.caption)


@dataclass(frozen=True, slots=True)
class GetAttachment:
    """Fetch attachment bytes through the authenticated bot route, never a URL."""

    bot_id: str | None = None
    chat: Mapping[str, str] | None = None
    file_id: str | None = None
    __api_method__ = "getAttachment"

    async def execute(self, bot: Bot) -> Any:
        return await bot.get_attachment(self.bot_id, self.chat, self.file_id)


@dataclass(frozen=True, slots=True)
class GetUpdates:
    """Adapted ``getUpdates`` method with VLook's bounded private update stream."""

    bot_id: str | None = None
    offset: str | None = None
    limit: int = 100
    timeout: int = 0
    request_timeout: float = 60
    include_attachments: bool = False
    __api_method__ = "getUpdates"

    async def execute(self, bot: Bot) -> Any:
        result = await bot.get_updates(
            self.bot_id,
            offset=self.offset,
            limit=self.limit,
            timeout=self.timeout,
            request_timeout=self.request_timeout,
            include_attachments=self.include_attachments,
        )
        if not isinstance(result, list):
            raise CloudBotValidationError("Cloud bot updates response is invalid.")
        return result


@dataclass(frozen=True, slots=True)
class AcknowledgeUpdates:
    """Explicit cumulative acknowledgement, separate from read-only polling."""

    bot_id: str | None = None
    through: str | None = None
    __api_method__ = "acknowledgeUpdates"

    async def execute(self, bot: Bot) -> str:
        return await bot.acknowledge_updates(self.bot_id, self.through)


@dataclass(frozen=True, slots=True)
class AnswerCallbackQuery:
    """Answer one private-chat callback; request_id is caller-stable for retries."""

    bot_id: str | None = None
    callback_query_id: str | None = None
    request_id: str | None = None
    text: str = ""
    show_alert: bool = False
    __api_method__ = "answerCallbackQuery"

    async def execute(self, bot: Bot) -> dict[str, Any]:
        return await bot.answer_callback_query(
            self.bot_id, self.callback_query_id, request_id=self.request_id,
            text=self.text, show_alert=self.show_alert,
        )


@dataclass(frozen=True, slots=True)
class GetMe:
    """Authenticate the token and resolve its public VLook bot identity."""

    timeout: float = 30
    __api_method__ = "getMe"

    async def execute(self, bot: Bot) -> dict[str, Any]:
        return await bot.get_me(timeout=self.timeout)


__all__ = ["AcknowledgeUpdates", "AnswerCallbackQuery", "GetAttachment", "GetMe", "GetUpdates",
           "SendDocument", "SendMessage", "SendPhoto"]

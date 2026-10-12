"""Run on the bot developer's server against a configured VLook test gateway.

The token-only interface requires a VLook gateway with the getMe contract.
Credentials come from the process environment; never print them or responses.
"""
import asyncio
import os

from aiovl import Bot, Dispatcher, Router


async def main():
    bot = Bot(os.environ["VLOOK_BOT_TOKEN"])
    router = Router()

    @router.on_command("/start")
    async def welcome(update):
        await bot.send_message(update["chat"], "Привет! Я бот VLook.",
                               request_id="welcome:" + update["updateId"],
                               reply_markup={
                                   "type": "inline",
                                   "rows": [[{"text": "Поздороваться", "callbackData": "hello"}]],
                               })

    @router.on_callback("hello")
    async def hello(update):
        query = update["callbackQuery"]
        # Reuse this id if the response is retried after a transport failure.
        await bot.answer_callback_query(
            query["id"], request_id="answer:" + query["id"], text="Готово")
        await bot.send_message(update["chat"], "Здравствуйте!",
                               request_id="callback-reply:" + update["updateId"])

    @router.on_text()
    async def echo(update):
        # A retry for this event reuses its request ID, including after restart.
        await bot.send_message(update["chat"], update["text"],
                               request_id="echo:" + update["updateId"])

    await Dispatcher(bot, router=router, timeout=0, acknowledge=True,
                     retry_transport=True).run_polling()


if __name__ == "__main__":
    asyncio.run(main())

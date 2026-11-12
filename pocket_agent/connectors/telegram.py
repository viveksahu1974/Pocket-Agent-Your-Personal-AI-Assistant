"""Telegram Bot API via long polling. No public URL needed."""
from __future__ import annotations

import asyncio
import os

from ..media import Attachment


async def run(agent, senders: dict, shutdown: asyncio.Event):
    from telegram.ext import Application, MessageHandler, filters

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    limit = int(agent.config["agent"]["max_attachment_bytes"])

    async def on_message(update, _context):
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if not msg or not user or not chat or user.is_bot:
            return
        # By default restrict to private DMs; do not process group traffic.
        if chat.type != "private":
            return
        if not agent.is_allowed("telegram", str(user.id)):
            await msg.reply_text("Access denied. Ask the owner to add your Telegram user ID.")
            return
        files = []
        media = msg.photo[-1] if msg.photo else msg.document
        if media and (getattr(media, "file_size", 0) or 0) <= limit:
            try:
                f = await media.get_file()
                data = bytes(await f.download_as_bytearray())
                if len(data) <= limit:
                    filename = getattr(media, "file_name", None) or ("photo.jpg" if msg.photo else "attachment")
                    mime = getattr(media, "mime_type", None) or ("image/jpeg" if msg.photo else "")
                    files.append(Attachment(filename, mime, data))
            except Exception:
                await msg.reply_text("Could not download the attachment; processing any text instead.")
        try:
            answer = await agent.receive("telegram", str(user.id), str(chat.id),
                                         msg.text or msg.caption or "", files)
            for i in range(0, len(answer), 3900):
                await msg.reply_text(answer[i:i+3900])
        except Exception:
            await msg.reply_text("Something went wrong while processing your message. Check the server logs.")

    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, on_message))

    async def send(chat_id: str, text: str):
        for i in range(0, len(text), 3900):
            await app.bot.send_message(chat_id=int(chat_id), text=text[i:i+3900])

    senders["telegram"] = send
    await app.initialize()
    try:
        await app.start()
        if app.updater is None:
            raise RuntimeError("Telegram updater not initialized")
        await app.updater.start_polling(drop_pending_updates=False)
        print("Telegram connector connected.")
        await shutdown.wait()
    finally:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()

"""Discord bot, responding only in DMs or to explicit mentions."""
from __future__ import annotations

import os

from ..media import Attachment


async def run(agent, senders: dict, _shutdown):
    import discord

    intents = discord.Intents.default()
    intents.message_content = True  # must be enabled for this bot in the Discord developer portal
    client = discord.Client(intents=intents)
    limit = int(agent.config["agent"]["max_attachment_bytes"])

    @client.event
    async def on_ready():
        print(f"Discord connected as {client.user}.")

    @client.event
    async def on_message(msg):
        if msg.author.bot or not client.user:
            return
        is_dm = isinstance(msg.channel, discord.DMChannel)
        is_mention = client.user in msg.mentions
        if not (is_dm or is_mention):
            return
        if not agent.is_allowed("discord", str(msg.author.id)):
            await msg.channel.send("Access denied. Ask the owner to add your Discord user ID.")
            return
        text = msg.content
        if is_mention:
            text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
        attachments = []
        remaining = limit
        for item in msg.attachments[:6]:
            if item.size > remaining:
                continue
            try:
                raw = await item.read(use_cached=True)
                remaining -= len(raw)
                attachments.append(Attachment(item.filename, item.content_type or "", raw))
            except Exception:
                pass
        try:
            answer = await agent.receive("discord", str(msg.author.id), str(msg.channel.id),
                                         text, attachments)
            for i in range(0, len(answer), 1900):
                await msg.channel.send(answer[i:i+1900], reference=msg if i == 0 else None)
        except Exception:
            await msg.channel.send("Message handling failed. Check the server logs.")

    async def send(chat_id: str, text: str):
        channel = client.get_channel(int(chat_id)) or await client.fetch_channel(int(chat_id))
        for i in range(0, len(text), 1900):
            await channel.send(text[i:i+1900])

    senders["discord"] = send
    try:
        await client.start(os.environ["DISCORD_BOT_TOKEN"])
    finally:
        await client.close()

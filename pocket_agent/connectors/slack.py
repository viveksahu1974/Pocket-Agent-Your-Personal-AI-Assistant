"""Slack Socket Mode: no externally exposed port required."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from ..media import Attachment


def _trusted_slack_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme == "https" and (p.hostname == "slack.com" or
                 (p.hostname or "").endswith(".slack.com"))
    except ValueError:
        return False


async def run(agent, senders: dict, _shutdown):
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

    token = os.environ["SLACK_BOT_TOKEN"]
    app = AsyncApp(token=token, signing_secret=os.getenv("SLACK_SIGNING_SECRET", "unused-for-socket-mode"))
    limit = int(agent.config["agent"]["max_attachment_bytes"])

    async def handle(event, client):
        if event.get("bot_id") or not event.get("user"):
            return
        if not agent.is_allowed("slack", str(event["user"])):
            await client.chat_postMessage(channel=event["channel"], text="Access denied. Ask the owner to add your Slack user ID.")
            return
        # Accept DMs and explicit app_mention; ignore unsolicited channel chatter.
        text = event.get("text", "")
        files = []
        remaining = limit
        for f in event.get("files", [])[:6]:
            url = f.get("url_private_download") or f.get("url_private") or ""
            size = int(f.get("size") or 0)
            if not _trusted_slack_url(url) or size > remaining:
                continue
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
                    r = await http.get(url, headers={"Authorization": f"Bearer {token}"})
                    r.raise_for_status()
                    if len(r.content) <= remaining:
                        files.append(Attachment(f.get("name", "attachment"),
                            f.get("mimetype", ""), r.content))
                        remaining -= len(r.content)
            except Exception:
                pass
        answer = await agent.receive("slack", str(event["user"]), str(event["channel"]), text, files)
        for i in range(0, len(answer), 3600):
            await client.chat_postMessage(channel=event["channel"], text=answer[i:i+3600])

    @app.event("message")
    async def on_dm(event, client):
        if event.get("channel_type") == "im" and not event.get("subtype"):
            await handle(event, client)

    @app.event("app_mention")
    async def on_mention(event, client):
        await handle(event, client)

    async def send(chat_id: str, text: str):
        for i in range(0, len(text), 3600):
            await app.client.chat_postMessage(channel=chat_id, text=text[i:i+3600])

    senders["slack"] = send
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("Starting Slack Socket Mode ...")
    try:
        await handler.start_async()
    finally:
        await handler.close_async()

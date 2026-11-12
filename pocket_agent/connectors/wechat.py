"""WeChat-compatible *bridge contract*, NOT unofficial direct WeChat account login.

An external, user-managed bridge translates WeChat messages to signed HTTP requests.
Direct personal WeChat bot APIs are not generally available through a supported official
interface. WeCom integrations may be implemented by adapting this bridge contract.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time

import httpx

from ..media import Attachment


def signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body,
                   hashlib.sha256).hexdigest()
    return "sha256=" + mac


def validate(secret: str, timestamp: str, body: bytes, sig: str, now: int | None = None) -> bool:
    if not secret or not timestamp.isdecimal():
        return False
    current = int(time.time()) if now is None else now
    if abs(current - int(timestamp)) > 300:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), sig)


def build_app(agent):
    from fastapi import FastAPI, HTTPException, Request

    app = FastAPI(title="Pocket Agent WeChat bridge ingress", docs_url=None, redoc_url=None)

    @app.post("/wechat/inbound")
    async def inbound(request: Request):
        body = await request.body()
        if len(body) > int(agent.config["agent"]["max_attachment_bytes"]) * 2 + 100000:
            raise HTTPException(413, "Payload too large")
        secret = os.getenv("WECHAT_BRIDGE_SECRET", "")
        if not validate(secret, request.headers.get("X-Agent-Timestamp", ""), body,
                        request.headers.get("X-Agent-Signature", "")):
            raise HTTPException(401, "Invalid bridge signature")
        try:
            data = json.loads(body)
            uid = str(data["user_id"])
            chat_id = str(data["chat_id"])
            text = str(data.get("text") or "")
            if not agent.is_allowed("wechat", uid):
                raise HTTPException(403, "WeChat user is not allowlisted")
            attachments = []
            for item in data.get("attachments", [])[:6]:
                raw = base64.b64decode(item["base64"], validate=True)
                attachments.append(Attachment(str(item.get("name", "file")),
                                              str(item.get("mime", "")), raw))
        except (KeyError, TypeError, ValueError, AttributeError, binascii.Error):
            raise HTTPException(400, "Invalid request payload")
        response = await agent.receive("wechat", uid, chat_id, text, attachments)
        return {"text": response}

    return app


async def run(agent, senders: dict, _shutdown):
    import uvicorn

    async def send(chat_id: str, text: str):
        url = os.getenv("WECHAT_SEND_URL", "")
        if not url:
            print("WeChat bridge outbound URL not set; result remains available via /job ID.")
            return
        body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-Agent-Timestamp": timestamp,
                   "X-Agent-Signature": signature(os.environ["WECHAT_BRIDGE_SECRET"], timestamp, body)}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, content=body, headers=headers)
            r.raise_for_status()

    senders["wechat"] = send
    wcfg = agent.config.get("wechat", {})
    config = uvicorn.Config(build_app(agent), host=wcfg.get("listen_host", "127.0.0.1"),
                            port=int(wcfg.get("listen_port", 8765)), log_level="warning")
    print("WeChat signed bridge ingress enabled at /wechat/inbound.")
    server = uvicorn.Server(config)
    await server.serve()

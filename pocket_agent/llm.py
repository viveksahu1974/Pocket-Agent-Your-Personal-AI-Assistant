"""Tiny native API adapters: OpenAI, Anthropic, and Ollama. No heavy LLM SDK."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Turn:
    text: str
    calls: list[ToolCall] = field(default_factory=list)


def parse_args(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class Models:
    def __init__(self, config: dict, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15))
        self.owns_client = client is None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        try:
            response = await self.client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            # Do not dump response headers (may contain credentials).
            detail = exc.response.text[:450]
            raise RuntimeError(f"Model API HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Cannot reach model API ({type(exc).__name__})") from exc

    async def complete(self, provider: str, model: str, messages: list[dict], tools: list[dict] | None = None) -> Turn:
        if provider == "openai":
            return await self._openai(model, messages, tools or [])
        if provider == "anthropic":
            return await self._anthropic(model, messages, tools or [])
        if provider == "ollama":
            return await self._ollama(model, messages, tools or [])
        raise ValueError(f"Unknown provider: {provider}")

    async def _openai(self, model: str, messages: list[dict], tools: list[dict]) -> Turn:
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Missing OPENAI_API_KEY")
        wire = []
        for m in messages:
            role = m["role"]
            if role == "user" and m.get("images"):
                parts = [{"type": "text", "text": m["content"] or "Describe the attached image."}]
                for img in m["images"]:
                    parts.append({"type": "image_url", "image_url": {
                        "url": f"data:{img['mime']};base64,{img['base64']}"}})
                wire.append({"role": "user", "content": parts})
            elif role == "assistant" and m.get("tool_calls"):
                wire.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {
                        "name": c["name"], "arguments": json.dumps(c["arguments"])
                    }} for c in m["tool_calls"]
                ]})
            elif role == "tool":
                wire.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                wire.append({"role": role, "content": m["content"]})
        payload = {"model": model, "messages": wire}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        obj = await self._post("https://api.openai.com/v1/chat/completions",
                               {"Authorization": f"Bearer {key}"}, payload)
        msg = obj["choices"][0]["message"]
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
        calls = [ToolCall(t.get("id") or uuid.uuid4().hex, t["function"]["name"],
                          parse_args(t["function"].get("arguments"))) for t in msg.get("tool_calls", [])]
        return Turn(str(content), calls)

    async def _anthropic(self, model: str, messages: list[dict], tools: list[dict]) -> Turn:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY")
        systems = [m["content"] for m in messages if m["role"] == "system"]
        wire = []
        for m in messages:
            role = m["role"]
            if role == "system":
                continue
            if role == "tool":
                wire.append({"role": "user", "content": [{"type": "tool_result",
                            "tool_use_id": m["tool_call_id"], "content": m["content"]}]})
            elif role == "assistant" and m.get("tool_calls"):
                content = ([{"type": "text", "text": m["content"]}] if m.get("content") else [])
                content += [{"type": "tool_use", "id": c["id"], "name": c["name"],
                             "input": c["arguments"]} for c in m["tool_calls"]]
                wire.append({"role": "assistant", "content": content})
            elif role == "user" and m.get("images"):
                blocks = [{"type": "text", "text": m["content"] or "Describe this image."}]
                for img in m["images"]:
                    blocks.append({"type": "image", "source": {"type": "base64",
                        "media_type": img["mime"], "data": img["base64"]}})
                wire.append({"role": "user", "content": blocks})
            else:
                wire.append({"role": role, "content": m["content"]})
        if not wire:
            wire = [{"role": "user", "content": "Hello"}]
        # Anthropic requires consecutive messages of the same role to be combined.
        merged = []
        for m in wire:
            if merged and merged[-1]["role"] == m["role"]:
                prev = merged[-1]["content"]
                incoming = m["content"]
                if isinstance(prev, str):
                    prev = [{"type": "text", "text": prev}]
                if isinstance(incoming, str):
                    incoming = [{"type": "text", "text": incoming}]
                merged[-1]["content"] = prev + incoming
            else:
                merged.append(m)
        payload = {"model": model, "max_tokens": 2048, "system": "\n\n".join(systems), "messages": merged}
        if tools:
            payload["tools"] = [{"name": t["function"]["name"],
                                 "description": t["function"].get("description", ""),
                                 "input_schema": t["function"]["parameters"]} for t in tools]
        obj = await self._post("https://api.anthropic.com/v1/messages",
                               {"x-api-key": key, "anthropic-version": "2023-06-01"}, payload)
        parts = obj.get("content", [])
        return Turn("\n".join(p.get("text", "") for p in parts if p.get("type") == "text"), [
            ToolCall(p.get("id") or uuid.uuid4().hex, p["name"], parse_args(p.get("input")))
            for p in parts if p.get("type") == "tool_use"
        ])

    async def _ollama(self, model: str, messages: list[dict], tools: list[dict]) -> Turn:
        base = self.config["provider"].get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        wire = []
        for m in messages:
            role = m["role"]
            item = {"role": role, "content": m.get("content", "")}
            if role == "user" and m.get("images"):
                item["images"] = [img["base64"] for img in m["images"]]
            if role == "assistant" and m.get("tool_calls"):
                item["tool_calls"] = [{"type": "function", "function": {
                    "name": c["name"], "arguments": c["arguments"]
                }} for c in m["tool_calls"]]
            if role == "tool":
                item["tool_name"] = m["name"]
            wire.append(item)
        payload = {"model": model, "messages": wire, "stream": False}
        if tools:
            payload["tools"] = tools
        obj = await self._post(f"{base}/api/chat", {}, payload)
        msg = obj.get("message", {})
        calls = []
        for t in msg.get("tool_calls") or []:
            function = t.get("function", {})
            if function.get("name"):
                calls.append(ToolCall(uuid.uuid4().hex, function["name"],
                                      parse_args(function.get("arguments"))))
        return Turn(msg.get("content") or "", calls)

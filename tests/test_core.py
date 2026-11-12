import asyncio
import json

import httpx
import pytest

from pocket_agent.agent import Agent
from pocket_agent.config import DEFAULT_CONFIG
from pocket_agent.connectors.wechat import signature, validate
from pocket_agent.llm import Models, ToolCall, Turn
from pocket_agent.media import Attachment, prepare_attachments
from pocket_agent.storage import Storage


class FakeModels:
    def __init__(self):
        self.calls = 0

    async def complete(self, provider, model, messages, tools=None):
        self.calls += 1
        if self.calls == 1 and any(t["function"]["name"] == "web_search" for t in (tools or [])):
            return Turn("", [ToolCall("tool-1", "web_search", {"query": "python news"})])
        return Turn("Here is the answer with sources.")

    async def close(self):
        pass


@pytest.fixture
def cfg():
    return json.loads(json.dumps(DEFAULT_CONFIG))


def test_memory_persists_and_aliases(tmp_path, cfg):
    cfg["security"]["aliases"] = {"telegram:123": "me", "discord:456": "me"}
    one = Storage(tmp_path / "test.db")
    one.set_fact("me", "language", "Hinglish")
    one.add_message("me", "user", "hello")
    two = Storage(tmp_path / "test.db")
    agent = Agent(cfg, two, FakeModels())
    assert agent.scope_for("telegram", "123") == agent.scope_for("discord", "456")
    assert two.facts("me") == {"language": "Hinglish"}
    assert two.recent("me")[0]["content"] == "hello"


def test_queue_claim_cancel_and_restart(tmp_path):
    db = Storage(tmp_path / "test.db")
    j1 = db.queue_job("a", "console", "local", "local", "one job")
    claimed = db.claim_job()
    assert claimed["id"] == j1
    assert db.cancel_job("a", j1)
    assert not db.complete_job(j1, result="should be ignored")
    j2 = db.queue_job("a", "console", "local", "local", "second job")
    assert db.claim_job()["id"] == j2
    db.requeue_interrupted()
    assert db.claim_job()["id"] == j2
    assert db.complete_job(j2, "done")
    assert db.get_job(j2)["status"] == "done"


def test_wechat_hmac_and_expiry():
    content = b'{"user_id":"a","chat_id":"b","text":"hi"}'
    sig = signature("secret", "1700000000", content)
    assert validate("secret", "1700000000", content, sig, now=1700000010)
    assert not validate("secret", "1700000000", content + b"x", sig, now=1700000010)
    assert not validate("secret", "1700000000", content, sig, now=1700010000)


def test_attachments_safe_extract():
    found = prepare_attachments([Attachment("notes.txt", "text/plain", b"Hello world"),
                                 Attachment("pic.png", "image/png", b"fake-png")], 2000)
    assert found[0].text == "Hello world"
    assert found[1].image["base64"] == "ZmFrZS1wbmc="
    assert "Attached image" in found[1].note


@pytest.mark.asyncio
async def test_commands_and_tool_loop(tmp_path, cfg, monkeypatch):
    db = Storage(tmp_path / "test.db")
    fake = FakeModels()
    agent = Agent(cfg, db, fake)
    assert "Saved memory" in await agent.receive("console", "local", "local", "/remember city = Delhi")
    assert "Delhi" in await agent.receive("console", "local", "local", "/memories")
    monkeypatch.setattr(agent, "_web_search", lambda q: asyncio.sleep(0, result="1. Example — https://example.com"))
    answer = await agent.receive("console", "local", "local", "search for python")
    assert "answer" in answer
    assert "Searched web" in "\n".join(db.events("console:local"))
    assert "Queued background" in await agent.receive("console", "local", "local", "/task make a detailed plan")
    assert db.list_jobs("console:local")[0]["status"] == "queued"
    assert "Memory removed" in await agent.receive("console", "local", "local", "/forget city")
    await agent.close()


@pytest.mark.asyncio
async def test_openai_adapter_normalizes_tools(monkeypatch, cfg):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    sent = {}

    def api(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Checking sources", "tool_calls": [{"id": "abc", "function": {
                "name": "web_search", "arguments": '{"query":"test"}'
            }}]
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
        models = Models(cfg, client)
        result = await models.complete("openai", "test-model", [{"role": "user", "content": "test"}], [])
        assert result.calls[0].name == "web_search"
        assert result.calls[0].arguments == {"query": "test"}
        assert sent["messages"][0]["content"] == "test"


@pytest.mark.asyncio
async def test_anthropic_adapter_and_tool_result_merge(monkeypatch, cfg):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    sent = {}

    def api(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "All done"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
        models = Models(cfg, client)
        result = await models.complete("anthropic", "test-model", [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "x", "name": "web_search", "arguments": {"query": "abc"}}]},
            {"role": "tool", "name": "web_search", "tool_call_id": "x", "content": "result"},
        ], [])
        assert result.text == "All done"
        assert sent["messages"][2]["role"] == "user"
        assert sent["messages"][2]["content"][0]["type"] == "tool_result"


@pytest.mark.asyncio
async def test_ollama_adapter(cfg):
    def api(request):
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message": {"content": "Local answer"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
        models = Models(cfg, client)
        result = await models.complete("ollama", "llama3.2", [{"role": "user", "content": "hello"}])
        assert result.text == "Local answer"

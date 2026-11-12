# Pocket Agent — self-hosted personal AI across chat apps

A small **Python 3.11+** personal agent: one process, SQLite memory, persistent background task queue, minimal core dependencies, and optional chat connectors. Supports **OpenAI**, **Anthropic/Claude**, and **Ollama** models. Source code is editable and runnable on Windows, Linux, or macOS.

> **Important:** Discord, Telegram, and Slack use official bot interfaces. **Personal WeChat does not offer a generally supported equivalent bot API**: the WeChat option uses a signed bridge contract that **you must supply or implement** (for example via an authorized enterprise/WeCom integration). Turning on the connector alone does **not** log you into WeChat. Third-party personal-account automation can violate platform terms and risk account restrictions.

## 1 — Quick start (Windows PowerShell)

```powershell
cd pocket-agent
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[all]"
python -m pocket_agent init
python -m pocket_agent doctor
python -m pocket_agent chat
```

For the **smallest installation**, use `pip install -e .` and the CLI or Ollama. Install only the integrations you need: `pip install -e ".[telegram,discord]"` for example.

Linux/macOS:

```bash
cd pocket-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
pocket-agent init
pocket-agent doctor
pocket-agent chat
```

To keep chat bots and background jobs online:

```bash
pocket-agent run
```

The wizard stores non-secret configuration in `agent-data/config.json` and API keys/bot tokens in `agent-data/.env` (mode `0600` on Unix). Both are Git-ignored. **Never commit, share, or print your tokens**. Environment variables already set in your shell override the `.env` values.

### Ollama (no cloud API key)

Install and start Ollama **on your own machine** and pull a model that supports the features you want:

```bash
ollama pull llama3.2
ollama serve
```

Some installations start Ollama automatically; don't launch a second server if it is already running. Use `http://127.0.0.1:11434` in the wizard. For **image understanding** use a vision-capable model, e.g. an appropriately installed multimodal Ollama model, then `/model ollama:YOUR_VISION_MODEL`. For **automatic tool calling** choose a model with reliable tool-use support; not all local models support tools or images. RAM requirements depend on the selected model; the agent process itself is lightweight.

### Cloud models

Choose `openai` or `anthropic` in the wizard, supply a valid provider API key, and a model identifier available to your account. Model IDs change: **sample defaults are examples, not claims of the newest supported model**. Switch interactively with `/model openai:MODEL`, `/model anthropic:MODEL`, or `/model ollama:MODEL`. Provider credentials must already be configured. This project calls the OpenAI Chat Completions API, Anthropic Messages API, and Ollama chat API directly, without a large AI SDK.

## 2 — Connecting channels

| Channel | What to create | Token(s) | How messages reach your bot |
| --- | --- | --- | --- |
| Telegram | Bot from **@BotFather** | `TELEGRAM_BOT_TOKEN` | Bot long-polls private messages; send your user ID and permit it in config. |
| Discord | Discord Developer Portal application and bot | `DISCORD_BOT_TOKEN` | Invite bot with message permissions; enable **Message Content Intent** if required. Replies to DMs and direct mentions. |
| Slack | Slack app with **Socket Mode** enabled | `SLACK_BOT_TOKEN` (`xoxb-`), `SLACK_APP_TOKEN` (`xapp-`) | Subscribe to `message.im` and `app_mention` events. Add bot scopes `chat:write`, `im:history`, `app_mentions:read`, and `files:read` for attachments. Reinstall app after scope changes. |
| WeChat | Your **own compatible, authorized bridge** | `WECHAT_BRIDGE_SECRET`, optional `WECHAT_SEND_URL` | Bridge POSTs authenticated messages to `/wechat/inbound`; outbound message delivery needs bridge implementation. |

**Access is private by default**: every channel user ID must appear in `security.allowed_users.<channel>` in `agent-data/config.json`. Without an ID, the agent rejects that channel's messages. Edit this JSON if you don't yet know your ID. To show your ID, use a platform's official developer tools or a trusted user-ID lookup bot. For Slack, the ID typically looks like `U...`; for Discord and Telegram, use numeric user IDs.

Use **private messages** when possible. Discord channel mentions and Slack app mentions are supported, but responses appear in those shared spaces. For one personal memory across your own accounts, explicitly set:

```json
"security": {
  "allowed_users": {
    "telegram": ["123456789"],
    "discord": ["987654321"],
    "slack": ["U123ABC"],
    "wechat": []
  },
  "aliases": {
    "telegram:123456789": "me",
    "discord:987654321": "me",
    "slack:U123ABC": "me"
  }
}
```

Keep the remaining configuration fields unchanged. **Only alias accounts that you own**, since aliased accounts share conversation memory and saved notes. User IDs are not necessarily globally unique, which is why the channel prefix is part of the mapping.

### WeChat signed bridge protocol

The connector exposes a local FastAPI endpoint at `http://127.0.0.1:8765/wechat/inbound` by default. If you expose it, use an HTTPS reverse proxy, restrict source access, and keep a strong shared secret. It does **not** bypass WeChat restrictions or provide a WeChat login client.

Your authorized bridge POSTs JSON:

```json
{
  "user_id": "wechat-user-identifier",
  "chat_id": "wechat-conversation-identifier",
  "text": "Hello agent",
  "attachments": [
    {"name": "photo.jpg", "mime": "image/jpeg", "base64": "..."}
  ]
}
```

Send `X-Agent-Timestamp: <unix seconds>` and `X-Agent-Signature: sha256=<lowercase hex>`. Signature input is `timestamp + "." + exact UTF-8 request body`; sign using **HMAC-SHA256** with `WECHAT_BRIDGE_SECRET`. Timestamps more than 5 minutes old are rejected. Response is `{"text":"Agent reply"}`, which the bridge must send to WeChat. For completed background job notifications, if `WECHAT_SEND_URL` is configured, Pocket Agent POSTs signed JSON `{"chat_id":"...", "text":"..."}` to that URL. The external bridge must implement this receiving endpoint and downstream delivery. No specific bridge provider is bundled.

**WeCom:** an officially authorized enterprise bot can be used if you implement an adapter translating its official inbound/outbound protocol to this interface. Consumer WeChat personal-account use is not guaranteed.

## 3 — Features and commands

Chat naturally in DM, or type:

| Command | Effect |
| --- | --- |
| `/help` | List commands. |
| `/model` | Show model; `/model ollama:llama3.2` selects one for your identity. |
| `/remember name = value` | Save an explicit long-lived memory. |
| `/memories` and `/forget name` | Inspect or delete explicit memory entries. |
| `/search solar eclipse date` | Search the web directly; requires your Brave Search API key. |
| `/task Investigate X and give me a report` | Queue work in SQLite and continue chatting. |
| `/jobs`, `/job JOB_ID`, `/cancel JOB_ID` | List, inspect, cancel a job. |
| `/steps` | View a concise log of tool actions and milestones, **not raw private model chain-of-thought**. |
| `/clear` | Erase conversation messages, summary, and activity events for your identity; saved facts/jobs remain. |

**Memory:** recent dialogue is sent as context, older messages are periodically condensed into a persistent summary, and `/remember` stores explicit key/value facts. Conversations and facts persist in SQLite across restarts. This is a pragmatic personal memory, **not semantic vector search** or unlimited context. For a full local wipe, stop the service and delete `agent-data/` (which also deletes config and credentials). Note that external AI and messaging providers have their own data policies.

**Background jobs:** jobs survive agent restarts in SQLite. The active worker claims and completes them; a crashed `running` job is requeued at startup. Results are stored even if a chat notification cannot be delivered. Jobs only execute **while `pocket-agent run` or `pocket-agent chat` is running**; no outside scheduler is silently installed. Job work is limited to model reasoning, safe built-in tool calls (search, memory), and writing responses; this agent **does not execute shell commands or control your computer**. Cancellations suppress delivery and storage of the completed result, but can't interrupt an already-running model API call. For uninterrupted hosting, run the process as an operating-system service or container.

**Files:** PNG/JPEG/WEBP/GIF images can be forwarded to multimodal models. TXT/MD/CSV/JSON/source text, DOCX text, and PDFs (optional `.[pdf]`) can be read and included in context. Attachments have a combined default 5 MB limit and are clipped to text limits. Embedded PDF images, scanned PDFs without extractable text, spreadsheets, audio, and video are **not** transcribed or interpreted by this MVP. A multimodal model is required for understanding images; supplying an image to a text-only Ollama model can fail. Media bytes are not permanently stored; any extracted text sent in chat is retained with conversation history until cleared.

**Web search:** add a Brave Search API key to `agent-data/.env` as `BRAVE_SEARCH_API_KEY="..."`. The `web_search` tool can be called automatically by a tool-capable model, or explicitly via `/search`. Sources are passed to the model as untrusted snippets. If the key is absent, the agent explains that search is disabled; **it does not fabricate fresh results**. Search usage and quotas are subject to Brave's terms.

**Reasoning transparency:** `/steps` reports tool calls, saved facts, and task state changes. Hidden model internal reasoning is not available, and this project does not attempt to extract it. For deep task transparency, consider extending job records with user-facing progress events.

## 4 — Run with Docker (optional)

```bash
docker compose up -d --build
docker compose logs -f
```

First run the wizard **outside the container** so `./agent-data/config.json` exists, or run `docker compose run --rm agent pocket-agent init` interactively (add `-it` if your Docker version requires it). The data directory is mounted for persistence. Docker builds the `all` dependencies; the default Python installation can be much smaller. On Linux Docker host networking is needed for an Ollama instance listening only on the host's loopback interface, or configure Ollama to listen on an accessible address securely. Don't publicly expose Ollama unauthenticated.

## 5 — Test and extend

```bash
pip install -e '.[dev]'
pytest -q
```

```
pocket_agent/
  __main__.py          CLI, wizard entrypoints
  config.py            Config and secret handling
  storage.py           SQLite history/memory/job queue
  llm.py               Native OpenAI / Anthropic / Ollama adapters
  media.py             Safe bounded file/image extraction
  agent.py             Agent prompt, tool loop, slash commands
  runtime.py           Concurrent connectors and background worker
  connectors/
    telegram.py        Telegram long polling
    discord.py         Discord DMs and mentions
    slack.py           Slack Socket Mode
    wechat.py          HMAC-protected bridge ingress/outbound
```

## 6 — Security and limitations

- Treat API keys, Slack tokens, and bridge signing secrets as sensitive. Keep `agent-data/` private and out of Git. Use unique secrets and rotate compromised ones.
- For privacy, use allowlisted DMs. In shared channels, replies and file excerpts may be visible to other participants.
- A bridge protocol is **not an official personal WeChat SDK**. Use only interfaces your account/platform permits.
- The web and uploaded documents can contain prompt injection attempts. They are marked as untrusted, but LLM attacks cannot be entirely eliminated. No arbitrary code-execution tool is provided.
- Real provider APIs, authentication, and chat platform events require your own credentials and an Internet connection. Unit tests mock external APIs and cannot prove live integrations.
- This is a **working starter MVP**, not a production-grade multi-tenant service. Harden logging, secret management, backpressure, webhook replay prevention, rate limits, audit trails, and bot permissions before sharing with others.
- If a provider retires an API, model, or platform permission, adapters may need updating. Validate against current provider docs before deployment.

**License:** MIT — you can build on this starter freely.

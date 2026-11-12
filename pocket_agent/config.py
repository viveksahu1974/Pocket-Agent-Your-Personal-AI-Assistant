"""Configuration and low-dependency interactive first-run wizard."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "provider": {"name": "ollama", "model": "llama3.2", "ollama_url": "http://127.0.0.1:11434"},
    "channels": {"telegram": False, "discord": False, "slack": False, "wechat": False},
    "wechat": {"listen_host": "127.0.0.1", "listen_port": 8765},
    "security": {"allowed_users": {"telegram": [], "discord": [], "slack": [], "wechat": []}, "aliases": {}},
    "agent": {"context_messages": 20, "max_tool_rounds": 4, "max_attachment_bytes": 5000000, "summary_every": 30},
}


def data_dir() -> Path:
    return Path(os.environ.get("POCKET_AGENT_HOME", "./agent-data")).expanduser().resolve()


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if not key.isidentifier() or key in os.environ:
            continue
        try:
            value = json.loads(raw) if raw.startswith('"') else raw
        except json.JSONDecodeError:
            value = raw
        if isinstance(value, str):
            os.environ[key] = value


def load_config(root: Path | None = None) -> dict:
    root = root or data_dir()
    path = root / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"No config found at {path}. Run: pocket-agent init")
    load_env(root / ".env")
    saved = json.loads(path.read_text(encoding="utf-8"))
    # Preserve defaults when a user adds new configuration keys in a newer release.
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(config.get(k), dict):
            config[k].update(v)
        else:
            config[k] = v
    return config


def _prompt(question: str, default: str = "") -> str:
    label = f" [{default}]" if default else ""
    answer = input(f"{question}{label}: ").strip()
    return answer or default


def run_wizard(root: Path | None = None) -> Path:
    import getpass

    root = root or data_dir()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    env_path = root / ".env"
    exists = config_path.exists()
    if exists:
        if _prompt("Existing config detected. Update it? (yes/no)", "yes").lower() != "yes":
            print(f"Keeping {config_path}")
            return config_path
        config = load_config(root)
    else:
        config = json.loads(json.dumps(DEFAULT_CONFIG))

    keys = (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BRAVE_SEARCH_API_KEY",
        "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET", "WECHAT_BRIDGE_SECRET", "WECHAT_SEND_URL",
    )
    # Retain credentials when updating a wizard configuration. Never echo secrets.
    secrets = {key: os.getenv(key, "") for key in keys if os.getenv(key)}

    def secret_prompt(key: str, optional: bool = False):
        old = secrets.get(key, "")
        note = " (leave empty to keep current value)" if old else (" (optional)" if optional else "")
        if key == "WECHAT_SEND_URL":
            answer = input(f"{key}{note}: ").strip()
        else:
            answer = getpass.getpass(f"{key}{note}: ").strip()
        if answer:
            secrets[key] = answer

    print("\nPocket Agent setup — press Enter to accept defaults or retain previous values.\n")
    provider = _prompt("Model provider (ollama/openai/anthropic)", config["provider"]["name"]).lower()
    if provider not in ("ollama", "openai", "anthropic"):
        raise ValueError("Choose ollama, openai, or anthropic")
    samples = {"ollama": "llama3.2", "openai": "gpt-4.1-mini", "anthropic": "claude-sonnet-4-5"}
    default_model = config["provider"]["model"] if provider == config["provider"]["name"] else samples[provider]
    config["provider"]["name"] = provider
    config["provider"]["model"] = _prompt("Model identifier", default_model)
    if provider == "ollama":
        config["provider"]["ollama_url"] = _prompt("Ollama URL", config["provider"].get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")
    else:
        secret_prompt("OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY")

    print("\nOptional chat channels. Re-running the wizard preserves your current keys and settings.")
    channel_keys = {
        "telegram": ["TELEGRAM_BOT_TOKEN"],
        "discord": ["DISCORD_BOT_TOKEN"],
        "slack": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET"],
        "wechat": ["WECHAT_BRIDGE_SECRET", "WECHAT_SEND_URL"],
    }
    for channel, channel_secrets in channel_keys.items():
        default = "yes" if config["channels"].get(channel) else "no"
        enabled = _prompt(f"Enable {channel}? (yes/no)", default).lower() in ("yes", "y")
        config["channels"][channel] = enabled
        if not enabled:
            continue
        for key in channel_secrets:
            secret_prompt(key, optional=key == "WECHAT_SEND_URL")
        old_ids = config["security"].get("allowed_users", {}).get(channel, [])
        people = _prompt(f"Allowed {channel} user ID(s), comma-separated (required for access)", ",".join(old_ids))
        config["security"].setdefault("allowed_users", {})[channel] = [x.strip() for x in people.split(",") if x.strip()]

    print("\nFor memory shared across apps, set security.aliases in config.json,")
    print('for example: {"telegram:123": "me", "discord:456": "me"}.')
    brave_default = "yes" if secrets.get("BRAVE_SEARCH_API_KEY") else "no"
    if _prompt("Enable Brave web search? (yes/no)", brave_default).lower() in ("yes", "y"):
        secret_prompt("BRAVE_SEARCH_API_KEY")
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(config_path, 0o600)
    env_path.write_text("# Pocket Agent private credentials; never commit this file.\n" + "".join(
        f"{key}={json.dumps(value)}\n" for key, value in secrets.items() if value
    ), encoding="utf-8")
    if os.name == "posix":
        os.chmod(env_path, 0o600)
    print(f"\nSaved config to {config_path}")
    print(f"Saved credentials to {env_path}")
    print("Run 'pocket-agent doctor' then 'pocket-agent run', or 'pocket-agent chat'.")
    return config_path


def check_config(config: dict) -> list[str]:
    warnings = []
    provider = config["provider"]["name"]
    required = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    if provider in required and not os.getenv(required[provider]):
        warnings.append(f"Missing {required[provider]} for {provider}")
    for channel, on in config["channels"].items():
        if not on:
            continue
        secrets = {
            "telegram": ["TELEGRAM_BOT_TOKEN"],
            "discord": ["DISCORD_BOT_TOKEN"],
            "slack": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
            "wechat": ["WECHAT_BRIDGE_SECRET"],
        }[channel]
        for secret in secrets:
            if not os.getenv(secret):
                warnings.append(f"{channel} enabled but missing {secret}")
        if not config["security"].get("allowed_users", {}).get(channel):
            warnings.append(f"{channel}: NO allowed user IDs; every message will be rejected")
    return warnings

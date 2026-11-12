"""CLI entrypoint: init, doctor, chat, run, jobs."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path

from .config import check_config, data_dir, load_config, run_wizard
from .storage import Storage


def main():
    parser = argparse.ArgumentParser(prog="pocket-agent", description="Lightweight self-hosted personal AI agent")
    parser.add_argument("command", nargs="?", choices=("init", "doctor", "chat", "run", "jobs"), default="doctor")
    args = parser.parse_args()
    if args.command == "init":
        run_wizard()
    elif args.command == "doctor":
        try:
            cfg = load_config()
        except FileNotFoundError as exc:
            parser.error(str(exc))
        print("Config:", data_dir() / "config.json")
        print("Provider:", cfg["provider"]["name"], cfg["provider"]["model"])
        packages = {"telegram": "telegram", "discord": "discord", "slack": "slack_bolt", "wechat": "fastapi"}
        for ch, on in cfg["channels"].items():
            if on:
                available = importlib.util.find_spec(packages[ch]) is not None
                print(f"{ch}: enabled; dependencies {'OK' if available else 'MISSING'}")
        problems = check_config(cfg)
        print("Warnings:", "\n- " + "\n- ".join(problems) if problems else "none")
        print("Network/API credentials were not tested; send a message to verify connectivity.")
    elif args.command == "jobs":
        store = Storage(data_dir() / "agent.db")
        for job in store.list_jobs("console:local", 30):
            print(f"{job['id']} [{job['status']}] {job['prompt'][:90]}")
    elif args.command == "chat":
        from .runtime import interactive_chat
        asyncio.run(interactive_chat())
    elif args.command == "run":
        from .runtime import run_server
        try:
            asyncio.run(run_server())
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()

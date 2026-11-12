"""Multi-connector process and persistent task worker."""
from __future__ import annotations

import asyncio
import contextlib

from .agent import Agent
from .config import check_config, data_dir, load_config
from .llm import Models
from .storage import Storage


async def background_worker(agent: Agent, senders: dict, shutdown: asyncio.Event):
    agent.db.requeue_interrupted()
    while not shutdown.is_set():
        job = agent.db.claim_job()
        if not job:
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            result = await agent.process_job(job)
            committed = agent.db.complete_job(job["id"], result=result)
            if committed:
                sender = senders.get(job["channel"])
                if sender:
                    try:
                        await sender(job["chat_id"], f"Background job {job['id']} finished:\n{result}")
                    except Exception as exc:
                        print(f"Unable to deliver job {job['id']} notification: {type(exc).__name__}; /job still works")
                elif job["channel"] == "console":
                    print(f"\n[Job {job['id']} complete]\n{result}\n")
        except Exception as exc:
            agent.db.complete_job(job["id"], error=f"{type(exc).__name__}: {exc}")


def create_agent() -> Agent:
    cfg = load_config()
    for warning in check_config(cfg):
        print("WARNING:", warning)
    return Agent(cfg, Storage(data_dir() / "agent.db"), Models(cfg))


async def run_server():
    agent = create_agent()
    senders = {}
    shutdown = asyncio.Event()
    tasks = [asyncio.create_task(background_worker(agent, senders, shutdown))]
    config = agent.config
    connected = []
    try:
        for name, on in config["channels"].items():
            if not on:
                continue
            try:
                if name == "telegram":
                    from .connectors import telegram as connector
                elif name == "discord":
                    from .connectors import discord as connector
                elif name == "slack":
                    from .connectors import slack as connector
                elif name == "wechat":
                    from .connectors import wechat as connector
                else:
                    continue
                # Import dependencies now so missing extras fail immediately.
                if name == "telegram":
                    import telegram.ext  # noqa: F401
                if name == "discord":
                    import discord  # noqa: F401
                if name == "slack":
                    import slack_bolt  # noqa: F401
                if name == "wechat":
                    import fastapi, uvicorn  # noqa: F401
                tasks.append(asyncio.create_task(connector.run(agent, senders, shutdown)))
                connected.append(name)
            except ImportError as exc:
                print(f"Cannot start {name}: missing optional package ({exc}). Install pip install '.[{name}]'")
        if not connected:
            print("No working channels enabled. Configure one or use: pocket-agent chat")
            return
        print("Pocket Agent running. Connectors:", ", ".join(connected))
        await asyncio.gather(*tasks)
    finally:
        shutdown.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await agent.close()


async def interactive_chat():
    agent = create_agent()
    shutdown = asyncio.Event()
    worker = asyncio.create_task(background_worker(agent, {}, shutdown))
    print("Pocket Agent chat. Type /help, or /exit to quit. Background work runs while this process is active.\n")
    try:
        while True:
            try:
                prompt = await asyncio.to_thread(input, "You> ")
            except (EOFError, KeyboardInterrupt):
                break
            if prompt.strip() in ("/exit", "/quit"):
                break
            result = await agent.receive("console", "local", "local", prompt)
            print("\nAgent>", result, "\n")
    finally:
        shutdown.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await agent.close()

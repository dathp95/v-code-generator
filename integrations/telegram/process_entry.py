"""Managed polling process. stdin commands; stdout contains allowlisted JSON only."""
import asyncio
import json
import logging
import sys
from pathlib import Path

from telegram.error import Conflict, InvalidToken, NetworkError
from core.crypto.key_loader import load_private_key
from core.services.license_service import LicenseService
from integrations.telegram.application import build_application
from integrations.telegram.config import BotConfig, VERSION
from storage.database import Database


def emit(event, **values):
    # Call sites supply fixed codes/IDs only, never exceptions or Telegram updates.
    allowed = {k: v for k, v in values.items() if k in ("code", "status", "request_id", "license_id", "name", "username")}
    print(json.dumps(dict(version=VERSION, event=event, **allowed)), flush=True)


async def run(config, stop, *, builder=build_application, output=emit):
    key = load_private_key(Path(config.key_path), config.password.encode("utf-8"))
    app = builder(config.token, config.admin_id, LicenseService(key), Database(config.db_path))
    app.bot_data["desktop_event"] = lambda **values: output("records", **values)
    fatal = False
    reconnecting = False

    def polling_error(error):
        nonlocal fatal, reconnecting
        if isinstance(error, Conflict):
            fatal = True
            output("error", code="conflict")
            stop.set()
        elif isinstance(error, NetworkError):
            reconnecting = True
            output("state", status="Reconnecting", code="network")
        else:
            fatal = True
            output("error", code="telegram")
            stop.set()

    # One event per processed update, including failures: DB is the source of truth.
    from telegram.ext import TypeHandler
    from telegram import Update

    async def changed(update, context):
        output("records", status="changed")

    app.add_handler(TypeHandler(Update, changed), group=100)
    initialized = started = polling = False
    try:
        # Initial network failures stay reconnecting, with cancellable bounded backoff.
        while not stop.is_set():
            try:
                await app.initialize()
                initialized = True
                break
            except NetworkError:
                output("state", status="Reconnecting", code="network")
                try:
                    await asyncio.wait_for(stop.wait(), 5)
                except asyncio.TimeoutError:
                    pass
        if stop.is_set():
            return
        await app.start()
        started = True
        while not stop.is_set():
            try:
                await app.updater.start_polling(error_callback=polling_error, bootstrap_retries=0)
                polling = True
                break
            except NetworkError:
                output("state", status="Reconnecting", code="network")
                try:
                    await asyncio.wait_for(stop.wait(), 5)
                except asyncio.TimeoutError:
                    pass
        if not polling:
            return
        if not fatal:
            output("identity", name=app.bot.first_name, username=app.bot.username)
            output("state", status="Connected")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except asyncio.TimeoutError:
                if reconnecting:
                    try:
                        await app.bot.get_me(read_timeout=5, connect_timeout=5)
                        reconnecting = False
                        output("state", status="Connected")
                        output("records", status="changed")
                    except Conflict as error:
                        polling_error(error)
                    except NetworkError:
                        pass
    except Conflict:
        output("error", code="conflict")
    except InvalidToken:
        output("error", code="credentials")
    except NetworkError:
        output("error", code="network")
    finally:
        if polling:
            await app.updater.stop()
        if started:
            await app.stop()
        if initialized:
            await app.shutdown()


async def entry():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Windows stdin pipes are read on a daemon thread, without blocking Qt or asyncio.
    import threading
    queue = asyncio.Queue()

    def reader():
        for line in sys.stdin:
            if len(line) > 65536:
                loop.call_soon_threadsafe(stop.set)
                return
            loop.call_soon_threadsafe(queue.put_nowait, line)
        loop.call_soon_threadsafe(queue.put_nowait, None)
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=reader, daemon=True).start()
    config = BotConfig.parse(json.loads(await queue.get()))

    async def commands():
        while not stop.is_set():
            try:
                message = json.loads(await queue.get())
            except (ValueError, TypeError):
                stop.set()
                return
            if not isinstance(message, dict) or message.get("version") != VERSION:
                stop.set()
                return
            if message.get("command") == "stop":
                stop.set()

    task = asyncio.create_task(commands())
    try:
        await run(config, stop)
    finally:
        task.cancel()
        emit("stopped")


def main():
    # Libraries can include token-bearing URLs or raw updates in logs.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(entry())
    except Exception:
        emit("error", code="startup")


if __name__ == "__main__":
    main()

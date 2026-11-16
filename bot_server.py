"""Run the Discord bot independently of the desktop and mobile apps."""

import asyncio
import logging
import os
from pathlib import Path
import signal

from chatbot_core import Bridge, safe_error
from server_api import start_api

logger = logging.getLogger(__name__)


class ServerEvents:
    def put(self, event):
        kind, _, payload = event
        if kind == "ready":
            logger.info("Discord connection ready")
        elif kind == "status":
            logger.info("%s", payload)
        elif kind == "error":
            logger.warning("%s", payload)


async def run_bot(token, state_dir, api_key=""):
    loop = asyncio.get_running_loop()
    bridge = Bridge(ServerEvents(), state_dir=state_dir)
    bridge.worker_loop = loop
    if os.name != "nt":
        loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    try:
        async with bridge:
            api = await start_api(bridge, api_key) if api_key else None
            try:
                await bridge.start(token)
            finally:
                if api is not None:
                    await api.cleanup()
    finally:
        if os.name != "nt":
            loop.remove_signal_handler(signal.SIGTERM)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Set DISCORD_BOT_TOKEN before starting the bot")
        return 2

    state_dir = os.environ.get("STATE_DIRECTORY") or Path.home() / ".discord-bot-server"
    try:
        api_key = os.environ.get("CHATBOT_API_KEY", "").strip()
        asyncio.run(run_bot(token, state_dir, api_key))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Discord bot stopped")
    except RuntimeError as exc:
        logger.error("Discord bot failed (%s)", type(exc).__name__)
        return 1
    except Exception as exc:
        logger.error("%s", safe_error(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
HiTony Server - Dual server launcher
Runs both WebSocket server (websockets lib) and HTTP server (FastAPI) concurrently
"""
import os
import sys

# Fix encoding issues on servers with ASCII locale
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('LANG', 'en_US.UTF-8')
os.environ.setdefault('LC_ALL', 'en_US.UTF-8')

import asyncio
import logging
import uvicorn
from app.ws_server import start_websocket_server
from app.scheduler import start_reminder_scheduler
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_http_server():
    """Run FastAPI HTTP server for admin endpoints"""
    try:
        config = uvicorn.Config(
            "app.main:app",
            host="0.0.0.0",
            port=8000,  # HTTP admin on port 8000
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()
    except Exception as e:
        logger.error(f"HTTP admin server failed: {e}")
        logger.warning("WebSocket server will continue running without HTTP admin")

async def main():
    """Run both servers concurrently"""
    logger.info("Starting HiTony servers...")
    logger.info(f"Python {sys.version}, encoding={sys.getdefaultencoding()}")
    logger.info(f"HTTP admin server will run on http://0.0.0.0:8000")
    logger.info(f"WebSocket server will run on ws://{settings.ws_host}:{settings.ws_port}")

    # return_exceptions=True: one failure won't kill the others
    results = await asyncio.gather(
        start_websocket_server(),
        run_http_server(),
        start_reminder_scheduler(),
        return_exceptions=True,
    )
    names = ["WebSocket", "HTTP admin", "Reminder scheduler"]
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"{names[i]} exited with error: {r}")

if __name__ == "__main__":
    asyncio.run(main())

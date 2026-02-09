#!/usr/bin/env python3
"""
EchoEar Server - Dual server launcher
Runs both WebSocket server (websockets lib) and HTTP server (FastAPI) concurrently
"""
import asyncio
import logging
import uvicorn
from app.ws_server import start_websocket_server
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def run_http_server():
    """Run FastAPI HTTP server for admin endpoints"""
    config = uvicorn.Config(
        "app.main_new:app",
        host="0.0.0.0",
        port=8000,  # HTTP admin on port 8000
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    """Run both servers concurrently"""
    logger.info("Starting EchoEar servers...")
    logger.info(f"HTTP admin server will run on http://0.0.0.0:8000")
    logger.info(f"WebSocket server will run on ws://{settings.ws_host}:{settings.ws_port}")

    # Run both servers concurrently
    await asyncio.gather(
        start_websocket_server(),
        run_http_server()
    )

if __name__ == "__main__":
    asyncio.run(main())

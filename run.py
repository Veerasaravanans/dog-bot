#!/usr/bin/env python
"""
DogBot Recon System - Startup Script

CRITICAL FIX for Windows MQTT: Uses uvicorn.Server with explicit SelectorEventLoop
to ensure add_reader/add_writer support for aiomqtt/paho-mqtt.
"""
import sys
import asyncio

async def main():
    """Main async entry point - runs uvicorn server with our event loop."""
    import uvicorn
    from backend.config import settings

    # Create uvicorn config
    config = uvicorn.Config(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level="info",
        loop="asyncio"  # Use asyncio event loop (not auto)
    )

    # Create server with our config
    server = uvicorn.Server(config)

    # Run server in our SelectorEventLoop
    await server.serve()


if __name__ == "__main__":
    from backend.config import settings

    print(f"[INFO] Starting DogBot Recon System on {settings.app_host}:{settings.app_port}")
    print(f"[INFO] Dashboard will be available at http://localhost:{settings.app_port}")

    # CRITICAL Windows Fix for MQTT (add_reader/add_writer support)
    #
    # ROOT CAUSE: Windows ProactorEventLoop doesn't support add_reader/add_writer
    # which aiomqtt/paho-mqtt requires.
    #
    # SOLUTION: Create SelectorEventLoop explicitly and run everything in it.
    # Using uvicorn.Server (not uvicorn.run) ensures OUR loop is used.
    if sys.platform == 'win32':
        print("[INFO] Windows detected: Using SelectorEventLoop for MQTT compatibility")

        # Create SelectorEventLoop explicitly
        loop = asyncio.SelectorEventLoop()

        # Verify loop type
        print(f"[DEBUG] Event loop type: {type(loop).__name__}")
        print(f"[DEBUG] Has add_reader: {hasattr(loop, 'add_reader')}")

        # Set as the current event loop
        asyncio.set_event_loop(loop)

        # Run everything in our SelectorEventLoop
        try:
            loop.run_until_complete(main())
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down...")
        finally:
            loop.close()
    else:
        # Linux/Mac: Use default asyncio.run()
        asyncio.run(main())

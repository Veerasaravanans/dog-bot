#!/usr/bin/env python
"""
Verification script to test that the fix properly creates SelectorEventLoop on Windows.
"""
import sys
import asyncio
import uvicorn
from backend.config import settings

print("=" * 80)
print("VERIFYING EVENT LOOP FIX")
print("=" * 80)

if sys.platform == 'win32':
    print("\n[Windows System Detected]")
    print("-" * 80)

    # Test the monkeypatch approach
    import uvicorn.loops.asyncio

    print(f"Original factory (before patch): {uvicorn.loops.asyncio.asyncio_loop_factory}")
    original_result = uvicorn.loops.asyncio.asyncio_loop_factory(use_subprocess=False)
    print(f"  Returns: {original_result.__name__}")

    # Apply the monkeypatch
    original_factory = uvicorn.loops.asyncio.asyncio_loop_factory

    def patched_asyncio_loop_factory(use_subprocess: bool = False):
        if sys.platform == "win32":
            return asyncio.SelectorEventLoop
        return original_factory(use_subprocess)

    uvicorn.loops.asyncio.asyncio_loop_factory = patched_asyncio_loop_factory

    print(f"\nPatched factory: {uvicorn.loops.asyncio.asyncio_loop_factory}")
    patched_result = uvicorn.loops.asyncio.asyncio_loop_factory(use_subprocess=False)
    print(f"  Returns: {patched_result.__name__}")

    # Create config with loop='asyncio' (will use our patched factory)
    config = uvicorn.Config(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level="info",
        loop="asyncio"
    )

    # Get the loop factory from config
    loop_factory = config.get_loop_factory()
    print(f"\nLoop factory from config: {loop_factory}")

    # Create a loop instance
    if loop_factory:
        test_loop = loop_factory()
        print(f"Loop type created: {test_loop.__class__.__name__}")
        print(f"Loop class module: {test_loop.__class__.__module__}")

        # Test if add_reader is available (required for MQTT)
        has_add_reader = hasattr(test_loop, 'add_reader') and callable(getattr(test_loop, 'add_reader', None))
        print(f"Has add_reader method: {has_add_reader}")

        if has_add_reader:
            # Test that add_reader doesn't raise NotImplementedError
            import socket
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                test_loop.add_reader(test_sock.fileno(), lambda: None)
                test_loop.remove_reader(test_sock.fileno())
                print("add_reader test: PASSED (no NotImplementedError)")
            except NotImplementedError as e:
                print(f"add_reader test: FAILED - {e}")
            finally:
                test_sock.close()

        test_loop.close()

        print("\n[Result]")
        print("-" * 80)
        # On Windows, SelectorEventLoop is _WindowsSelectorEventLoop
        is_selector = "SelectorEventLoop" in test_loop.__class__.__name__
        if is_selector and has_add_reader:
            print("SUCCESS: SelectorEventLoop is properly configured!")
            print("MQTT (aiomqtt) should work correctly with this configuration.")
        else:
            print("FAILURE: Event loop is not properly configured for MQTT.")
    else:
        print("ERROR: loop_factory is None")
else:
    print("\n[Non-Windows System]")
    print("-" * 80)
    print("This fix is only needed on Windows. Standard uvicorn.run() will work fine.")

print("\n" + "=" * 80)

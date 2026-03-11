# Windows AsyncIO Fix - Summary

## Problem
MQTT connectivity fails on Windows with `NotImplementedError` in `add_reader()` callback, despite setting event loop policy.

## Root Cause
uvicorn's `asyncio_loop_factory()` HARDCODES `ProactorEventLoop` on Windows, completely bypassing any event loop policy set via `asyncio.set_event_loop_policy()`.

**The problematic code in uvicorn/loops/asyncio.py:**
```python
def asyncio_loop_factory(use_subprocess: bool = False):
    if sys.platform == "win32" and not use_subprocess:
        return asyncio.ProactorEventLoop  # <-- HARDCODED!
    return asyncio.SelectorEventLoop
```

This is then instantiated directly: `loop = loop_factory()`, never calling the policy's `new_event_loop()`.

## Solution
Monkeypatch uvicorn's `asyncio_loop_factory()` to return `SelectorEventLoop` on Windows.

## Code Changes

### File: `run.py`

**BEFORE:**
```python
#!/usr/bin/env python
"""
DogBot Recon System - Startup Script
Properly configures asyncio event loop for Windows before starting uvicorn.
"""
import sys
import asyncio

# CRITICAL: Set Windows event loop policy BEFORE any asyncio code runs
# This must happen before uvicorn imports and creates its event loop
if sys.platform == 'win32':
    # ProactorEventLoop (Windows default) doesn't support add_reader/add_writer
    # which aiomqtt/paho-mqtt requires. Use SelectorEventLoop instead.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("[INFO] Windows detected: Using WindowsSelectorEventLoopPolicy for MQTT compatibility")

if __name__ == "__main__":
    import uvicorn
    from backend.config import settings

    print(f"[INFO] Starting DogBot Recon System on {settings.app_host}:{settings.app_port}")
    print(f"[INFO] Dashboard will be available at http://localhost:{settings.app_port}")

    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level="info"
    )
```

**AFTER:**
```python
#!/usr/bin/env python
"""
DogBot Recon System - Startup Script
Properly configures asyncio event loop for Windows before starting uvicorn.
"""
import sys
import asyncio

if __name__ == "__main__":
    import uvicorn
    from backend.config import settings

    print(f"[INFO] Starting DogBot Recon System on {settings.app_host}:{settings.app_port}")
    print(f"[INFO] Dashboard will be available at http://localhost:{settings.app_port}")

    # CRITICAL Windows Fix for MQTT (add_reader/add_writer support)
    #
    # ROOT CAUSE: uvicorn's asyncio_loop_factory() HARDCODES ProactorEventLoop
    # for Windows, completely BYPASSING any event loop policy set via:
    # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    #
    # WHY IT FAILS: Setting the policy doesn't work because uvicorn's internal
    # asyncio_loop_factory() directly returns the ProactorEventLoop CLASS,
    # never calling the policy's new_event_loop() method.
    #
    # SOLUTION: Pass loop='asyncio' but monkeypatch uvicorn's loop factory
    # to return SelectorEventLoop on Windows instead of ProactorEventLoop.
    if sys.platform == 'win32':
        print("[INFO] Windows detected: Configuring SelectorEventLoop for MQTT compatibility")

        # Monkeypatch uvicorn's asyncio_loop_factory to return SelectorEventLoop on Windows
        import uvicorn.loops.asyncio

        original_factory = uvicorn.loops.asyncio.asyncio_loop_factory

        def patched_asyncio_loop_factory(use_subprocess: bool = False):
            # Always return SelectorEventLoop on Windows (ignore use_subprocess)
            # This is required for aiomqtt/paho-mqtt add_reader/add_writer support
            if sys.platform == "win32":
                return asyncio.SelectorEventLoop
            return original_factory(use_subprocess)

        uvicorn.loops.asyncio.asyncio_loop_factory = patched_asyncio_loop_factory

        # Now use standard uvicorn.run() with loop='asyncio'
        # It will use our patched factory that returns SelectorEventLoop
        uvicorn.run(
            "backend.main:app",
            host=settings.app_host,
            port=settings.app_port,
            reload=False,
            log_level="info",
            loop="asyncio"  # Use asyncio (not auto) to ensure our patch is used
        )
    else:
        # Non-Windows: Use standard uvicorn.run()
        uvicorn.run(
            "backend.main:app",
            host=settings.app_host,
            port=settings.app_port,
            reload=False,
            log_level="info"
        )
```

### File: `backend/main.py`

**BEFORE:**
```python
import logging
import sys
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Windows-specific: Fix for aiomqtt compatibility
# The ProactorEventLoop on Windows doesn't support add_reader/add_writer
# which aiomqtt (paho-mqtt) requires. Use SelectorEventLoop instead.
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.config import settings
```

**AFTER:**
```python
import logging
import sys
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
```

**Change**: Removed the ineffective policy setting from `backend/main.py` since it doesn't work with uvicorn.

## Verification

Run the verification script:
```bash
python verify_fix.py
```

Expected output:
```
SUCCESS: SelectorEventLoop is properly configured!
MQTT (aiomqtt) should work correctly with this configuration.
```

## Key Takeaways

1. **Event loop policies don't work with uvicorn** on Windows because uvicorn bypasses them
2. **Monkeypatching is the cleanest solution** for this specific problem
3. **The fix is contained in run.py** and doesn't affect other parts of the codebase
4. **Testing confirms** that SelectorEventLoop is properly configured with add_reader/add_writer support

## Files Modified
- `run.py` - Added monkeypatch to fix event loop creation
- `backend/main.py` - Removed ineffective policy setting

## Files Created
- `WINDOWS_ASYNCIO_FIX.md` - Detailed technical documentation
- `FIX_SUMMARY.md` - This summary
- `test_event_loop_policy.py` - Diagnostic script showing the problem
- `verify_fix.py` - Verification script confirming the fix works

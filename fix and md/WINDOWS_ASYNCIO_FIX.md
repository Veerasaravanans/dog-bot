# Windows AsyncIO Event Loop Fix for MQTT Connectivity

## Problem Statement

Despite setting `asyncio.WindowsSelectorEventLoopPolicy()` in `run.py` BEFORE importing uvicorn, the application still fails with:

```
[INFO] Windows detected: Using WindowsSelectorEventLoopPolicy for MQTT compatibility
...
[asyncio] ERROR: Exception in callback AbstractEventLoop.add_reader...
raise NotImplementedError
```

## Root Cause Analysis

### Why Setting the Policy Doesn't Work

The event loop policy is being **completely bypassed** by uvicorn's internal loop creation mechanism. Here's the chain of events:

1. **run.py sets the policy** (line 11-14):
   ```python
   asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
   ```

2. **uvicorn.run() is called** (line 24-29)

3. **uvicorn creates a Server and calls server.run()**:
   - File: `uvicorn/server.py:66`
   - Code: `asyncio_run(self.serve(sockets=sockets), loop_factory=self.config.get_loop_factory())`

4. **config.get_loop_factory() returns the wrong factory**:
   - File: `uvicorn/config.py:486-497`
   - For `loop="auto"` (the default), it imports: `uvicorn.loops.auto:auto_loop_factory`
   - This falls back to: `uvicorn.loops.asyncio:asyncio_loop_factory`

5. **asyncio_loop_factory() HARDCODES the loop type**:
   - File: `uvicorn/loops/asyncio.py:8-11`
   - Code:
     ```python
     def asyncio_loop_factory(use_subprocess: bool = False) -> Callable[[], asyncio.AbstractEventLoop]:
         if sys.platform == "win32" and not use_subprocess:
             return asyncio.ProactorEventLoop  # <-- HARDCODED!
         return asyncio.SelectorEventLoop
     ```

6. **uvicorn._compat.asyncio_run() creates the loop**:
   - File: `uvicorn/_compat.py:51-54`
   - Code:
     ```python
     if loop_factory is None:
         loop = asyncio.new_event_loop()
     else:
         loop = loop_factory()  # <-- Directly instantiates ProactorEventLoop
     ```

### The Key Issue

The problem is in step 5: **uvicorn returns the loop CLASS directly** (`asyncio.ProactorEventLoop`), which is then instantiated in step 6. This **completely bypasses** the event loop policy we set, because:

- The policy would only be used if uvicorn called `asyncio.new_event_loop()`
- Instead, uvicorn calls `ProactorEventLoop()` directly
- The policy's `new_event_loop()` method is never invoked

### Why This Matters

Windows has two event loop implementations:

1. **ProactorEventLoop** (Windows default since Python 3.8):
   - Uses Windows I/O Completion Ports (IOCP)
   - Does NOT support `add_reader()` / `add_writer()` methods
   - Raises `NotImplementedError` if you try to use them

2. **SelectorEventLoop**:
   - Uses `select.select()` for I/O
   - DOES support `add_reader()` / `add_writer()` methods
   - Required by aiomqtt/paho-mqtt for socket monitoring

## The Solution

### Implementation (run.py)

The fix **monkeypatches** uvicorn's `asyncio_loop_factory()` before calling `uvicorn.run()`:

```python
if sys.platform == 'win32':
    print("[INFO] Windows detected: Configuring SelectorEventLoop for MQTT compatibility")

    # Monkeypatch uvicorn's asyncio_loop_factory
    import uvicorn.loops.asyncio

    original_factory = uvicorn.loops.asyncio.asyncio_loop_factory

    def patched_asyncio_loop_factory(use_subprocess: bool = False):
        # Always return SelectorEventLoop on Windows
        if sys.platform == "win32":
            return asyncio.SelectorEventLoop
        return original_factory(use_subprocess)

    uvicorn.loops.asyncio.asyncio_loop_factory = patched_asyncio_loop_factory

    # Use standard uvicorn.run() with loop='asyncio'
    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level="info",
        loop="asyncio"  # Ensures our patched factory is used
    )
```

### Why This Works

1. **Before uvicorn creates any loops**, we replace its `asyncio_loop_factory` function
2. When uvicorn calls `config.get_loop_factory()`, it returns our patched function
3. Our patched function returns `asyncio.SelectorEventLoop` (the class)
4. uvicorn instantiates `SelectorEventLoop()` instead of `ProactorEventLoop()`
5. The SelectorEventLoop supports `add_reader()` / `add_writer()`
6. MQTT (aiomqtt) works correctly

### Why loop='asyncio' is Important

We specify `loop='asyncio'` to ensure uvicorn uses the `asyncio_loop_factory` path instead of trying to use `uvloop` (if installed). This guarantees our monkeypatch is applied.

## Alternative Solutions Considered

### 1. Setting Event Loop Policy (DOESN'T WORK)
```python
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```
**Why it fails**: uvicorn bypasses the policy by directly instantiating loop classes.

### 2. Pre-creating and Setting Event Loop (DOESN'T WORK)
```python
loop = asyncio.SelectorEventLoop()
asyncio.set_event_loop(loop)
uvicorn.run(...)
```
**Why it fails**: uvicorn calls `asyncio_run()` which creates a NEW loop, ignoring any existing loop.

### 3. Using uvicorn.Config with Custom Loop Factory (COMPLEX)
```python
def custom_factory(use_subprocess=False):
    return asyncio.SelectorEventLoop

config = uvicorn.Config("backend.main:app", loop=custom_factory)
server = uvicorn.Server(config=config)
server.run()
```
**Why we didn't use it**: When passing a callable to `Config(loop=...)`, uvicorn tries to import it as a string, causing errors. Would require creating a separate module file.

### 4. Monkeypatching (CHOSEN SOLUTION)
**Pros**:
- Simple, reliable, and contained in run.py
- Works with standard uvicorn.run() API
- No external files needed
- Explicitly documents the issue

**Cons**:
- Modifies uvicorn's internal behavior (but only for this process)
- Could break if uvicorn changes its internals (unlikely)

## Testing

### Verification Script

Run `verify_fix.py` to confirm the fix works:

```bash
python verify_fix.py
```

Expected output:
```
[Windows System Detected]
--------------------------------------------------------------------------------
Original factory (before patch): <function asyncio_loop_factory at ...>
  Returns: ProactorEventLoop

Patched factory: <function patched_asyncio_loop_factory at ...>
  Returns: _WindowsSelectorEventLoop

Loop factory from config: <class 'asyncio.windows_events._WindowsSelectorEventLoop'>
Loop type created: _WindowsSelectorEventLoop
Has add_reader method: True
add_reader test: PASSED (no NotImplementedError)

[Result]
--------------------------------------------------------------------------------
SUCCESS: SelectorEventLoop is properly configured!
MQTT (aiomqtt) should work correctly with this configuration.
```

### Manual Testing

1. Start the application: `python run.py`
2. Verify the log shows:
   ```
   [INFO] Windows detected: Configuring SelectorEventLoop for MQTT compatibility
   ```
3. Check that MQTT connection succeeds without NotImplementedError
4. Verify aiomqtt can subscribe and publish messages

## Technical Details

### Event Loop Class Hierarchy

On Windows, `asyncio.SelectorEventLoop` is actually an alias for `_WindowsSelectorEventLoop`:

```python
# In asyncio/windows_events.py
class _WindowsSelectorEventLoop(asyncio.SelectorEventLoop):
    # Windows-specific SelectorEventLoop implementation
    pass
```

Both are valid and support `add_reader()` / `add_writer()`.

### Python Version Compatibility

This fix works on:
- Python 3.8+ (when ProactorEventLoop became the Windows default)
- Python 3.10 (tested)
- Python 3.11+

The monkeypatch is safe and doesn't interfere with:
- Other event loop operations
- WebSocket connections
- FastAPI async endpoints
- Background tasks

## Summary

**Problem**: Setting `asyncio.WindowsSelectorEventLoopPolicy()` doesn't work because uvicorn bypasses the policy.

**Root Cause**: uvicorn's `asyncio_loop_factory()` directly returns `ProactorEventLoop` class on Windows, which is then instantiated without using the policy.

**Solution**: Monkeypatch `uvicorn.loops.asyncio.asyncio_loop_factory()` to return `SelectorEventLoop` on Windows before calling `uvicorn.run()`.

**Result**: MQTT (aiomqtt) works correctly on Windows with proper `add_reader()` / `add_writer()` support.

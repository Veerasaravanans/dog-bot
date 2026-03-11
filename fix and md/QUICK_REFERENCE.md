# Quick Reference - Windows AsyncIO MQTT Fix

## The Problem in One Sentence
Setting `asyncio.set_event_loop_policy()` doesn't work because uvicorn directly instantiates `ProactorEventLoop` class, bypassing the policy entirely.

## The Fix in One Code Block

```python
# In run.py, before calling uvicorn.run()
if sys.platform == 'win32':
    import uvicorn.loops.asyncio

    original_factory = uvicorn.loops.asyncio.asyncio_loop_factory

    def patched_asyncio_loop_factory(use_subprocess: bool = False):
        if sys.platform == "win32":
            return asyncio.SelectorEventLoop
        return original_factory(use_subprocess)

    uvicorn.loops.asyncio.asyncio_loop_factory = patched_asyncio_loop_factory

    uvicorn.run("backend.main:app", ..., loop="asyncio")
```

## Why It Works
1. Monkeypatches uvicorn's loop factory BEFORE uvicorn creates any event loops
2. Returns `SelectorEventLoop` class (supports add_reader/add_writer)
3. uvicorn instantiates our patched factory's return value
4. MQTT works because SelectorEventLoop supports socket monitoring

## Testing
```bash
# Verify the fix works
python verify_fix.py

# Test MQTT compatibility
python test_mqtt_compatibility.py

# Start the application
python run.py
```

## Files Changed
- **run.py**: Added monkeypatch (28 lines of code)
- **backend/main.py**: Removed ineffective policy setting

## What Was Wrong Before

**Code that DOESN'T work:**
```python
# This is completely ignored by uvicorn!
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
uvicorn.run("backend.main:app", ...)
```

**Why:** uvicorn's internal code does this:
```python
# In uvicorn/loops/asyncio.py
def asyncio_loop_factory(use_subprocess=False):
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop  # <-- Hardcoded!
    return asyncio.SelectorEventLoop

# Then later:
loop = loop_factory()  # Direct instantiation, bypasses policy
```

## Technical Details

| Event Loop Type | add_reader/add_writer | Works with MQTT |
|---|---|---|
| ProactorEventLoop (Windows default) | ❌ NotImplementedError | ❌ No |
| SelectorEventLoop | ✅ Supported | ✅ Yes |

## See Also
- `WINDOWS_ASYNCIO_FIX.md` - Detailed technical documentation
- `FIX_SUMMARY.md` - Before/after code comparison
- `test_event_loop_policy.py` - Diagnostic showing the problem

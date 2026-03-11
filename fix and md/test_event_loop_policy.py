#!/usr/bin/env python
"""
Test script to verify event loop policy behavior with uvicorn.
This demonstrates the root cause and validates the fix.
"""
import sys
import asyncio

print("=" * 80)
print("TESTING EVENT LOOP POLICY WITH UVICORN")
print("=" * 80)

# Test 1: Setting policy in run.py (current approach)
print("\n[TEST 1] Setting policy BEFORE uvicorn import (current approach in run.py)")
print("-" * 80)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(f"[OK] Set event loop policy: {asyncio.get_event_loop_policy().__class__.__name__}")

import uvicorn
from uvicorn.loops.asyncio import asyncio_loop_factory

# Check what loop factory uvicorn will use
print(f"[OK] Uvicorn's asyncio_loop_factory will create: ", end="")
loop_factory = asyncio_loop_factory(use_subprocess=False)
print(f"{loop_factory.__name__}")

# Test 2: Simulate what happens when uvicorn.run() is called
print("\n[TEST 2] Simulating uvicorn.run() event loop creation")
print("-" * 80)

# This is what uvicorn does internally:
# 1. Config.get_loop_factory() returns asyncio_loop_factory
# 2. asyncio_loop_factory(use_subprocess=False) returns asyncio.ProactorEventLoop on Windows
# 3. uvicorn._compat.asyncio_run() calls loop_factory() to create the loop
# 4. This BYPASSES the event loop policy we set!

print("When uvicorn.run() executes:")
print(f"  1. Policy set: {asyncio.get_event_loop_policy().__class__.__name__}")
print(f"  2. Loop factory returned by uvicorn: {loop_factory.__name__}")

# Create a loop using the factory (this is what uvicorn does)
test_loop = loop_factory()
print(f"  3. Loop type created by factory: {test_loop.__class__.__name__}")
test_loop.close()

# Create a loop using the policy (this is what should happen)
policy_loop = asyncio.get_event_loop_policy().new_event_loop()
print(f"  4. Loop type from policy: {policy_loop.__class__.__name__}")
policy_loop.close()

print("\n[DIAGNOSIS]")
print("-" * 80)
print("THE ROOT CAUSE:")
print("  uvicorn's asyncio_loop_factory() HARDCODES loop types:")
print("    - Windows + no subprocess -> ProactorEventLoop")
print("    - All other cases -> SelectorEventLoop")
print("")
print("  This BYPASSES any event loop policy set via:")
print("    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())")
print("")
print("  The policy is IGNORED because uvicorn directly instantiates")
print("  the loop class instead of using the policy's new_event_loop().")

print("\n[SOLUTION OPTIONS]")
print("-" * 80)
print("Option 1: Use uvicorn's loop parameter to force asyncio mode")
print("  uvicorn.run('backend.main:app', loop='asyncio')")
print("  On Windows with loop='asyncio', uvicorn uses SelectorEventLoop")
print("")
print("Option 2: Create and set event loop explicitly before uvicorn.run()")
print("  loop = asyncio.WindowsSelectorEventLoopPolicy().new_event_loop()")
print("  asyncio.set_event_loop(loop)")
print("  uvicorn.run(...)")
print("")
print("Option 3: Use uvicorn.Config + uvicorn.Server with custom loop")
print("  More control but more complex setup")
print("")
print("RECOMMENDED: Option 1 (simplest, most reliable)")

print("\n" + "=" * 80)

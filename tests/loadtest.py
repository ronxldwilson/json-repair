"""
Concurrent load test for json-repair staging.
Tests deterministic-only and mixed (deterministic + LLM) workloads.
"""

import asyncio
import aiohttp
import time
import json
import os
import sys
import statistics

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOADTEST_URL")
if not BASE_URL:
    print("Error: set LOADTEST_URL env var or pass URL as first argument")
    sys.exit(1)
API_KEY = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LOADTEST_API_KEY", "")

DETERMINISTIC_CASES = [
    {"broken_json": "{'name': 'Alice', 'age': 30}"},
    {"broken_json": "{name: 'Bob', active: True}"},
    {"broken_json": '{"items": [1,2,3,]}'},
    {"broken_json": "/* comment */ {\"a\": 1}"},
    {"broken_json": "{'list': [1, 2, 3], 'nested': {'key': 'value',}}"},
    {"broken_json": "{\"x\": undefined, \"y\": None}"},
    {"broken_json": "callback_123({\"data\": [1,2,3]});"},
    {"broken_json": "```json\n{\"a\": 1}\n```"},
]

LLM_CASES = [
    {
        "broken_json": '{"items [{"id: 1 "name": "widget"} {"id": 2, "name": "gadget"]',
        "schema_dict": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"}
                        }
                    }
                }
            }
        }
    },
]

HEADERS = {"Content-Type": "application/json", "X-API-Key": API_KEY}


async def send_request(session, payload, request_id):
    start = time.monotonic()
    try:
        async with session.post(f"{BASE_URL}/repair", json=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            elapsed = time.monotonic() - start
            body = await resp.json()
            return {
                "id": request_id,
                "status": resp.status,
                "method": body.get("method", "?"),
                "valid": body.get("valid", False),
                "elapsed": elapsed,
                "error": None,
            }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "id": request_id,
            "status": 0,
            "method": "error",
            "valid": False,
            "elapsed": elapsed,
            "error": str(e),
        }


async def run_wave(session, label, payloads, concurrency):
    print(f"\n{'='*70}")
    print(f"  {label}  |  {len(payloads)} requests @ concurrency {concurrency}")
    print(f"{'='*70}")

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(payload, rid):
        async with semaphore:
            return await send_request(session, payload, rid)

    wall_start = time.monotonic()
    tasks = [bounded(p, i) for i, p in enumerate(payloads)]
    results = await asyncio.gather(*tasks)
    wall_time = time.monotonic() - wall_start

    successes = [r for r in results if r["status"] == 200 and r["valid"]]
    errors = [r for r in results if r["status"] != 200 or not r["valid"]]
    times = [r["elapsed"] for r in results]

    print(f"\n  Total:     {len(results)} requests in {wall_time:.1f}s")
    print(f"  Success:   {len(successes)}/{len(results)}")
    if errors:
        print(f"  Errors:    {len(errors)}")
        for e in errors[:5]:
            print(f"    #{e['id']}: status={e['status']} error={e['error']}")
    print(f"  Throughput: {len(results)/wall_time:.1f} req/s")
    print(f"  Latency:")
    print(f"    min:  {min(times)*1000:.0f}ms")
    print(f"    p50:  {statistics.median(times)*1000:.0f}ms")
    print(f"    p95:  {sorted(times)[int(len(times)*0.95)]*1000:.0f}ms")
    print(f"    p99:  {sorted(times)[int(len(times)*0.99)]*1000:.0f}ms")
    print(f"    max:  {max(times)*1000:.0f}ms")

    methods = {}
    for r in results:
        methods[r["method"]] = methods.get(r["method"], 0) + 1
    print(f"  Methods:   {methods}")

    return {"label": label, "total": len(results), "success": len(successes),
            "errors": len(errors), "wall_time": wall_time,
            "rps": len(results)/wall_time,
            "p50": statistics.median(times), "p95": sorted(times)[int(len(times)*0.95)],
            "max": max(times)}


async def main():
    print(f"Load test target: {BASE_URL}")
    print(f"Warming up...")

    conn = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=conn) as session:
        # Warmup
        await send_request(session, DETERMINISTIC_CASES[0], 0)
        print("Warm. Starting tests.\n")

        summary = []

        # Wave 1: 10 concurrent deterministic
        payloads = [DETERMINISTIC_CASES[i % len(DETERMINISTIC_CASES)] for i in range(10)]
        summary.append(await run_wave(session, "10 concurrent deterministic", payloads, 10))

        # Wave 2: 25 concurrent deterministic
        payloads = [DETERMINISTIC_CASES[i % len(DETERMINISTIC_CASES)] for i in range(25)]
        summary.append(await run_wave(session, "25 concurrent deterministic", payloads, 25))

        # Wave 3: 50 concurrent deterministic
        payloads = [DETERMINISTIC_CASES[i % len(DETERMINISTIC_CASES)] for i in range(50)]
        summary.append(await run_wave(session, "50 concurrent deterministic", payloads, 50))

        # Wave 4: 100 concurrent deterministic
        payloads = [DETERMINISTIC_CASES[i % len(DETERMINISTIC_CASES)] for i in range(100)]
        summary.append(await run_wave(session, "100 concurrent deterministic", payloads, 100))

        # Wave 5: 5 concurrent LLM-heavy
        payloads = [LLM_CASES[0] for _ in range(5)]
        summary.append(await run_wave(session, "5 concurrent LLM", payloads, 5))

        # Wave 6: Mixed — 20 deterministic + 3 LLM concurrent
        payloads = [DETERMINISTIC_CASES[i % len(DETERMINISTIC_CASES)] for i in range(20)]
        payloads += [LLM_CASES[0] for _ in range(3)]
        summary.append(await run_wave(session, "Mixed: 20 deterministic + 3 LLM", payloads, 23))

        # Summary table
        print(f"\n{'='*70}")
        print(f"  SUMMARY")
        print(f"{'='*70}")
        print(f"{'Wave':<40} {'RPS':>6} {'p50':>8} {'p95':>8} {'max':>8} {'Err':>4}")
        print(f"{'-'*40} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*4}")
        for s in summary:
            print(f"{s['label']:<40} {s['rps']:>5.1f} {s['p50']*1000:>7.0f}ms {s['p95']*1000:>7.0f}ms {s['max']*1000:>7.0f}ms {s['errors']:>4}")


asyncio.run(main())

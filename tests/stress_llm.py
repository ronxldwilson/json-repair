"""
LLM-tier stress test — broken JSON too mangled for deterministic repair.
"""

import asyncio
import aiohttp
import time
import os
import statistics
import sys

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOADTEST_URL")
if not BASE_URL:
    print("Error: set LOADTEST_URL env var or pass URL as first argument")
    sys.exit(1)
API_KEY = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LOADTEST_API_KEY", "")
HEADERS = {"Content-Type": "application/json", "X-API-Key": API_KEY}

HARD_CASES = [
    {
        "name": "scrambled_keys_and_values",
        "broken_json": '''{"users [{"name "John Doe" age 32 "email john@example.com "active" true}, {name: "Jane" age "28 email: jane@test.com active false}] "total_count 2 "page: 1}''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "users": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "age": {"type": "integer"},
                    "email": {"type": "string"}, "active": {"type": "boolean"}
                }}},
                "total_count": {"type": "integer"},
                "page": {"type": "integer"}
            }
        }
    },
    {
        "name": "nested_corruption",
        "broken_json": '''{company: Acme Corp, departments: [{name: "Engineering, head: {name: "Alice, title: CTO, reports: [{name Bob, role: "backend dev"}, {name: Carol role "frontend dev}]}, budget: 500000}, {name: Sales, head: {name: Dave, title: VP Sales, reports: [{name Eve, role: "account exec}, name: Frank, role: SDR}]} budget 300000}]}''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "departments": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "head": {"type": "object", "properties": {
                        "name": {"type": "string"}, "title": {"type": "string"},
                        "reports": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"}, "role": {"type": "string"}
                        }}}
                    }},
                    "budget": {"type": "integer"}
                }}}
            }
        }
    },
    {
        "name": "mixed_formats_hell",
        "broken_json": '''// config dump from prod
{
  server: {
    host: '192.168.1.1'
    port: 8080,
    ssl: True
    cert_path: "/etc/ssl/cert.pem
    workers = 4
  }
  database: {
    'url': 'postgres://user:p@ss@localhost/db'
    pool_size: 10;
    timeout: None
    replicas: [
      {host: "replica1.internal" port: 5433 weight: 0.6}
      {host: "replica2.internal" port: 5434 weight: 0.4}
    ]
  },
  cache: {
    backend: "redis",
    ttl: 3600,
    prefix: "app:",
    nodes = ["redis1:6379", "redis2:6379", "redis3:6379"
  }
  features: {
    dark_mode: True,
    beta_users: ['alice', 'bob', 'carol'
    max_upload_mb: .5
    rate_limit: 1_000
  }
}''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "server": {"type": "object", "properties": {
                    "host": {"type": "string"}, "port": {"type": "integer"},
                    "ssl": {"type": "boolean"}, "cert_path": {"type": "string"}, "workers": {"type": "integer"}
                }},
                "database": {"type": "object", "properties": {
                    "url": {"type": "string"}, "pool_size": {"type": "integer"},
                    "timeout": {}, "replicas": {"type": "array"}
                }},
                "cache": {"type": "object", "properties": {
                    "backend": {"type": "string"}, "ttl": {"type": "integer"},
                    "prefix": {"type": "string"}, "nodes": {"type": "array"}
                }},
                "features": {"type": "object", "properties": {
                    "dark_mode": {"type": "boolean"}, "beta_users": {"type": "array"},
                    "max_upload_mb": {"type": "number"}, "rate_limit": {"type": "integer"}
                }}
            }
        }
    },
    {
        "name": "truncated_api_response",
        "broken_json": '''{"status":"ok","data":{"orders":[{"id":"ORD-001","customer":{"name":"Alice Smith","address":{"street":"123 Main St","city":"Springfield","state":"IL","zip":"62701"}},"items":[{"sku":"WIDGET-A","qty":3,"price":12.99},{"sku":"GADGET-B","qty":1,"price":45.00},{"sku":"THING-C","qty":2,"price":7.50}],"total":91.97,"status":"shipped"},{"id":"ORD-002","customer":{"name":"Bob Jones","address":{"street":"456 Oak Ave","city":"Portland","state":"OR","zip":"97201"}},"items":[{"sku":"WIDGET-A","qty":1,"price":12.99},{"sku":"DOOHICK''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "data": {"type": "object", "properties": {
                    "orders": {"type": "array", "items": {"type": "object", "properties": {
                        "id": {"type": "string"},
                        "customer": {"type": "object"},
                        "items": {"type": "array"},
                        "total": {"type": "number"},
                        "status": {"type": "string"}
                    }}}
                }}
            }
        }
    },
    {
        "name": "yaml_ish_json",
        "broken_json": '''{
  name: My Application
  version: 2.1.0
  dependencies:
    - name: express
      version: 4.18.2
      dev: false
    - name: typescript
      version: 5.0.0
      dev: true
    - name: jest
      version: 29.5.0
      dev: true
  scripts:
    build: tsc --build
    test: jest --coverage
    start: node dist/index.js
  repository:
    type: git
    url: https://github.com/user/app
}''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "string"},
                "dependencies": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "version": {"type": "string"}, "dev": {"type": "boolean"}
                }}},
                "scripts": {"type": "object"},
                "repository": {"type": "object", "properties": {
                    "type": {"type": "string"}, "url": {"type": "string"}
                }}
            }
        }
    },
    {
        "name": "double_encoded_mess",
        "broken_json": r'''{"payload":"{\"users\":[{\"id\":1,\"name\":\"O\'Brien\",\"bio\":\"She said \\\"hello\\\" and left\",\"tags\":[\"admin\",\"power-user\"]},{\"id\":2,\"name\":\"José García\",\"bio\":\"Line1\nLine2\nLine3\",\"tags\":[\"user\"]}],\"meta\":{\"page\":1,\"total\":42}}","timestamp":"2024-01-15T10:30:00Z","status":ok}''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "payload": {"type": "string"},
                "timestamp": {"type": "string"},
                "status": {"type": "string"}
            }
        }
    },
    {
        "name": "html_contaminated",
        "broken_json": '''<response>{"products": [{"id": 1, "name": "Widget <b>Pro</b>", "description": "The best widget for <i>professional</i> use", "price": "$29.99", "in_stock": true, "categories": ["tools", "professional"], "rating": 4.5/5}, {"id": 2, "name": "Gadget &amp; Gizmo", "description": "A combo pack\nwith free shipping", "price": 15.00, "in_stock": false, "categories": ["bundles"], "rating": 3.8/5}], "pagination": {"page": 1, "per_page": 10, "total": 2}}</response>''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "products": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "integer"}, "name": {"type": "string"},
                    "description": {"type": "string"}, "price": {"type": "number"},
                    "in_stock": {"type": "boolean"}, "categories": {"type": "array"},
                    "rating": {"type": "number"}
                }}},
                "pagination": {"type": "object"}
            }
        }
    },
    {
        "name": "log_dump_extraction",
        "broken_json": '''[INFO 2024-01-15 10:30:00] Processing batch...
{"batch_id": "B-001", results: [
  {id: 1, status: "success", duration_ms: 234, output: {message: "Processed 150 records
encountered 3 warnings: [duplicate key, missing field, type mismatch]"}},
  {id: 2, status: 'failed', duration_ms: 1502, error: {code: 500, message: 'Connection timed out after 1500ms
retrying in 30s...'}},
  {id: 3, status: success, duration_ms: 89, output: {message: "All clear"}}
], summary: {total: 3, passed: 2, failed: 1, avg_duration: 608.33}}
[INFO 2024-01-15 10:30:02] Batch complete.''',
        "schema_dict": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "results": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "integer"}, "status": {"type": "string"},
                    "duration_ms": {"type": "number"},
                    "output": {"type": "object"}, "error": {"type": "object"}
                }}},
                "summary": {"type": "object"}
            }
        }
    },
]


_completed = 0
_total = 0
_lock = asyncio.Lock()


async def send_request(session, case, request_id):
    global _completed
    payload = {"broken_json": case["broken_json"]}
    if "schema_dict" in case:
        payload["schema_dict"] = case["schema_dict"]

    ts = time.strftime("%H:%M:%S")
    print(f"  [{ts}] #{request_id:<3} START  {case['name']}", flush=True)
    start = time.monotonic()
    try:
        async with session.post(f"{BASE_URL}/repair", json=payload, headers=HEADERS,
                                timeout=aiohttp.ClientTimeout(total=120)) as resp:
            elapsed = time.monotonic() - start
            body = await resp.json()
            async with _lock:
                _completed += 1
                done = _completed
            status = "PASS" if body.get("valid") else "FAIL"
            ts = time.strftime("%H:%M:%S")
            print(f"  [{ts}] #{request_id:<3} {status}   {case['name']:<30} {body.get('method','?'):<16} {elapsed:>6.1f}s  ({done}/{_total})", flush=True)
            return {
                "id": request_id,
                "name": case["name"],
                "status": resp.status,
                "method": body.get("method", "?"),
                "valid": body.get("valid", False),
                "elapsed": elapsed,
                "error": None,
                "repaired": body.get("repaired_json", "")[:120],
            }
    except Exception as e:
        elapsed = time.monotonic() - start
        async with _lock:
            _completed += 1
            done = _completed
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] #{request_id:<3} ERROR  {case['name']:<30} {str(e)[:40]:<16} {elapsed:>6.1f}s  ({done}/{_total})", flush=True)
        return {
            "id": request_id, "name": case["name"], "status": 0,
            "method": "error", "valid": False,
            "elapsed": elapsed, "error": str(e), "repaired": "",
        }


async def run_test(session, label, cases, concurrency):
    global _completed, _total
    _completed = 0
    _total = len(cases)

    print(f"\n{'='*80}")
    print(f"  {label}  |  {len(cases)} requests @ concurrency {concurrency}")
    print(f"{'='*80}\n")

    sem = asyncio.Semaphore(concurrency)
    async def bounded(c, i):
        async with sem:
            return await send_request(session, c, i)

    wall_start = time.monotonic()
    results = await asyncio.gather(*[bounded(c, i) for i, c in enumerate(cases)])
    wall_time = time.monotonic() - wall_start

    times = [r["elapsed"] for r in results]
    ok = sum(1 for r in results if r["valid"])
    methods = {}
    for r in results:
        methods[r["method"]] = methods.get(r["method"], 0) + 1

    print(f"\n  --- SUMMARY ---")
    print(f"  Results:    {ok}/{len(results)} valid")
    print(f"  Wall time:  {wall_time:.1f}s")
    print(f"  Throughput: {len(results)/wall_time:.2f} req/s")
    print(f"  Latency:    min={min(times):.1f}s  p50={statistics.median(times):.1f}s  max={max(times):.1f}s")
    print(f"  Methods:    {methods}")
    return results


async def wait_for_idle(session, threshold_ms=500, checks=3):
    """Wait until the server responds consistently fast, meaning it's not busy with prior load."""
    print(f"  Waiting for server to be idle (< {threshold_ms}ms for {checks} consecutive checks)...")
    consecutive = 0
    warmup_payload = {"broken_json": "{'a': 1}"}
    while consecutive < checks:
        start = time.monotonic()
        try:
            async with session.post(f"{BASE_URL}/repair", json=warmup_payload, headers=HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                elapsed_ms = (time.monotonic() - start) * 1000
                if resp.status == 200 and elapsed_ms < threshold_ms:
                    consecutive += 1
                    print(f"    check {consecutive}/{checks}: {elapsed_ms:.0f}ms ✓", flush=True)
                else:
                    consecutive = 0
                    print(f"    check reset: {elapsed_ms:.0f}ms (status={resp.status}), waiting...", flush=True)
        except Exception as e:
            consecutive = 0
            print(f"    check reset: {e}, waiting...", flush=True)
        await asyncio.sleep(2)
    print(f"  Server is idle. Starting stress test.\n")


async def main():
    print(f"LLM Stress Test — {BASE_URL}")
    conn = aiohttp.TCPConnector(limit=50)
    async with aiohttp.ClientSession(connector=conn) as session:
        await wait_for_idle(session)

        # Phase 1: Each case sequentially to see which ones actually hit LLM
        r1 = await run_test(session, "Phase 1: Sequential (baseline)", HARD_CASES, 1)

        # Phase 2: All 8 cases at once
        await run_test(session, "Phase 2: All 8 concurrent", HARD_CASES, 8)

        # Phase 3: Double up — 16 requests (each case x2) all concurrent
        doubled = HARD_CASES * 2
        await run_test(session, "Phase 3: 16 concurrent (each case x2)", doubled, 16)

        # Phase 4: Triple — 24 requests, all concurrent, max pressure on llama slots
        tripled = HARD_CASES * 3
        await run_test(session, "Phase 4: 24 concurrent (each case x3)", tripled, 24)

        # Summary of which cases hit LLM vs deterministic
        print(f"\n{'='*80}")
        print(f"  CASE DIFFICULTY PROFILE (from Phase 1)")
        print(f"{'='*80}")
        for r in r1:
            tier = "EASY" if r["method"] == "deterministic" else "HARD"
            print(f"  [{tier}] {r['name']:<30} → {r['method']}")


asyncio.run(main())

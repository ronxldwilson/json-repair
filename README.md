# JSON Repair

HTTP service that fixes broken JSON using a 4-tier repair pipeline: deterministic parser → type coercion → targeted snippet LLM → full LLM fallback. Uses Qwen2.5-Coder-1.5B via llama.cpp with grammar-constrained generation. No external API calls — runs fully offline.

## How it works

1. **Deterministic repair** (~98% of cases, <1ms) — regex/string-based fixes for missing commas, quotes, brackets, Python literals, multiline strings, number formats, etc.
2. **Type coercion** — reads schema validation errors and surgically fixes type mismatches (int→string, etc.)
3. **Snippet LLM** — sends only the 200-char error window to the LLM instead of the full JSON
4. **Full LLM fallback** — regenerates entire JSON with grammar-constrained generation to match the schema

## Quick start

```bash
docker compose up -d
```

Two containers:
- **llama** — llama-server with 8 parallel inference slots (~283 MB RAM)
- **api** — FastAPI service on port 8080

### Using an external LLM API (Groq, OpenAI, Together, etc.)

Skip the local llama container and use a fast external API instead. Set these env vars on the `api` service:

```yaml
environment:
  - LLM_PROVIDER=external
  - EXTERNAL_API_URL=https://api.groq.com/openai/v1
  - EXTERNAL_API_KEY=gsk_...
  - EXTERNAL_MODEL=llama-3.3-70b-versatile
```

Any OpenAI-compatible chat/completions API works. With `LLM_PROVIDER=external`, the llama container is not needed — you can run just the `api` service:

```bash
docker compose up -d api
```

| Env var | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `local` | `local` (llama container) or `external` (API) |
| `EXTERNAL_API_URL` | `https://api.groq.com/openai/v1` | Base URL (without `/chat/completions`) |
| `EXTERNAL_API_KEY` | — | Bearer token for the API |
| `EXTERNAL_MODEL` | `llama-3.3-70b-versatile` | Model name to send in requests |

## API

### `POST /repair`

```bash
curl -X POST http://localhost:8080/repair \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <key>" \
  -d '{
    "broken_json": "{\"name\": \"John Doe,\n  \"age\": 30\n  \"active\": True\n}",
    "schema_dict": {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "active": {"type": "boolean"}
      },
      "required": ["name", "age", "active"]
    }
  }'
```

Response:
```json
{
  "repaired_json": "{\"name\": \"John Doe\", \"age\": 30, \"active\": true}",
  "valid": true,
  "method": "deterministic"
}
```

The `method` field tells you which tier resolved it: `deterministic`, `deterministic+coerce`, `snippet(N)`, or `llm`.

### `GET /health`

```bash
curl http://localhost:8080/health
```

## Using with Pydantic

Pass `model_json_schema()` directly as `schema_dict` — the service resolves `$defs`/`$ref` internally.

```python
from pydantic import BaseModel
import json
from urllib.request import Request, urlopen

class Address(BaseModel):
    street: str
    city: str
    zip: int

class Person(BaseModel):
    name: str
    age: int
    email: str
    address: Address
    hobbies: list[str]
    active: bool

body = {
    "broken_json": broken_string,
    "schema_dict": Person.model_json_schema(),
}
req = Request(
    "http://localhost:8080/repair",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)
result = json.loads(urlopen(req, timeout=120).read())
repaired = json.loads(result["repaired_json"])
```

## What it fixes

The deterministic parser handles these error types without any LLM call:

- Missing/trailing/leading commas
- Single quotes → double quotes
- Unquoted keys
- Python literals (`True`/`False`/`None`/`undefined` → `true`/`false`/`null`)
- Markdown code fences and preamble text
- Comments (`//` and `/* */`)
- Multiline strings (real newlines inside string values)
- Missing colons between keys and values
- Missing closing quotes
- Non-standard numbers (`.75`, `0x1F`, `1_000`, `0042`, `Infinity`, `NaN`)
- Trailing decimals and exponents (`2.` → `2.0`, `2e` → `2e0`, `2e+` → `2e+0`)
- Leading-zero numbers quoted as strings (`0789` → `"0789"`)
- Unclosed brackets/braces
- Mismatched bracket types (`}` inside `[]` → `]`, `]` inside `{}` → `}`)
- Unescaped control characters and backslashes
- Ellipsis removal (`[1,2,3,...]` → `[1,2,3]`)
- Empty/missing values (`{"key": }` → `{"key": null}`)
- JSONP unwrapping (`callback({})` → `{}`, `callback(2)` → `2`)
- MongoDB extended JSON (`ObjectId("123")` → `"123"`)
- Truncated strings and key-value pairs
- Bare escape sequences outside strings

## Benchmark results

242/242 test cases pass — 52 internal (27 test + 25 validation) and 190 external cases from three open-source libraries.

### Internal suite (52 cases with Pydantic schemas)

| Tier | Cases | Avg Latency |
|---|---|---|
| Deterministic | 45 (87%) | <1ms |
| Deterministic + coerce | 2 (4%) | <1ms |
| Snippet LLM | 1 (2%) | ~2s |
| Full LLM | 4 (8%) | ~3-30s |

### External suite (190 cases from josdejong/jsonrepair, mangiucugna/json_repair, RyanMarcus/dirty-json)

| Tier | Cases | Avg Latency |
|---|---|---|
| Deterministic | 159 (84%) | <1ms |
| Snippet LLM | 24 (13%) | ~0.8s |
| Full LLM | 7 (4%) | ~2.7s |

## Project structure

```
scripts/
  repair.py   — deterministic repair + schema validation + type coercion
  llm.py      — llama-server interaction (grammar, schema, snippet repair)
  server.py   — FastAPI app + 4-tier orchestration
tests/
  cases/      — 27 test cases with schemas
  validation/ — 25 validation cases (ProductCard, SupplierCard)
  external/   — 190 cross-library test cases (josdejong, mangiucugna, dirty-json)
  benchmark.py — benchmark runner (supports REPAIR_URL / REPAIR_API_KEY env vars)
```

## Schema is required for nested structures

Without a schema, the GBNF grammar enforces valid JSON syntax but the 1.5B model may collapse nested structures (e.g. moving sibling keys inside child objects). Always pass a schema for anything beyond flat key-value JSON.

| Mode | Valid JSON | Correct Structure |
|---|---|---|
| No schema (GBNF grammar) | Yes | Flat only |
| With schema | Yes | Yes |

## Resource usage

| Metric | Value |
|---|---|
| Llama container RAM | ~283 MB (8 slots × 1024 ctx) |
| Deterministic repair | <1ms |
| LLM repair | ~5-40s (3 threads, 8 slots) |
| Docker image (llama) | ~1.4 GB |
| Docker image (api) | ~200 MB |

## Architecture

```
┌─────────┐     ┌───────────┐     ┌──────────────┐
│  Client  │────▶│  FastAPI   │────▶│ llama-server │
│          │◀────│  (api)     │◀────│  (llama)     │
└─────────┘     └───────────┘     └──────────────┘
   :8080          :8080              :8776 (internal)
```

## Docker images

```
ronxldwilson/json-repair:llama  # llama-server + model
ronxldwilson/json-repair:api    # FastAPI service
```

## Performance tuning

The llama container runs with 8 parallel inference slots (`-np 8`) and a total context of 8192 tokens (`-c 8192`), giving each slot 1024 tokens. This configuration was chosen based on load testing:

### Why 8 slots instead of 4

The model weights (~1.1 GB) are loaded once and shared read-only across all slots. Each slot only needs its own KV cache (~12 MB at 1024 context). Doubling slots from 4 to 8 costs ~96 MB extra KV cache but doubles concurrent capacity.

| Config | Slots | Context/slot | RAM | 16 concurrent LLM |
|---|---|---|---|---|
| Old (4 slots, 4096 ctx) | 4 | 4096 | 984 MB | 14/16 pass (2 timeouts) |
| **New (8 slots, 8192 ctx)** | **8** | **1024** | **283 MB** | **16/16 pass** |

### Concurrent load test results

Tested with 8 hard cases (scrambled keys, nested corruption, YAML-ish, HTML-contaminated, etc.) that all require full LLM repair:

| Concurrency | Pass rate | Wall time | Throughput |
|---|---|---|---|
| 1 (sequential) | 8/8 | 125s | 0.06 req/s |
| 8 concurrent | 8/8 | 93s | 0.09 req/s |
| 16 concurrent | 16/16 | 113s | 0.14 req/s |
| 24 concurrent | 16/24 | 120s | 0.20 req/s |

Deterministic requests (98% of real traffic) are unaffected by LLM load — they return in <1ms regardless of how many LLM requests are queued. At 100 concurrent deterministic requests: 420 req/s with p95 of 223ms (network-bound).

The 24-concurrent ceiling is CPU-bound (3 threads generating tokens for 18 LLM requests simultaneously). The 120s timeout catches the slowest requests. For higher throughput, increase CPU allocation or offload to an external API.

### Tuning the slot/context tradeoff

| Flag | Effect | Guidance |
|---|---|---|
| `-np N` | Number of parallel inference slots | More slots = more concurrent requests, but each gets less CPU time |
| `-c N` | Total context window (divided across slots) | 1024/slot is sufficient for JSON repair. Increase if repairing very large JSON (>4KB) |
| `-t N` | CPU threads for inference | Set to (cores - 1) to leave headroom for other services |

To override without rebuilding the image, set `command:` in docker-compose.yml.

### Load test scripts

```bash
# Deterministic + light LLM load test
python tests/loadtest.py <url> [api-key]

# Heavy LLM stress test (8 hard cases, phases 1-4)
python tests/stress_llm.py <url> [api-key]
```

Both scripts can also be configured via `LOADTEST_URL` and `LOADTEST_API_KEY` env vars.

## Design decisions

- **Deterministic first** — regex/string parser handles ~98% of cases in <1ms, LLM is a fallback
- **No thinking/reasoning models** — pure autoregressive (Qwen2.5-Coder), prompt-in/JSON-out
- **llama-server over llama-cli** — model loads once, stays in RAM, each repair is an HTTP call
- **Grammar constraint is essential** — `json_schema` response format forces valid JSON at the token generation level

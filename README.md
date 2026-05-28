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
- **llama** — llama-server with model baked in (~1.4 GB RAM)
- **api** — FastAPI service on port 8080

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

- Missing/trailing commas
- Single quotes → double quotes
- Unquoted keys
- Python literals (`True`/`False`/`None` → `true`/`false`/`null`)
- Markdown code fences and preamble text
- Comments (`//` and `/* */`)
- Multiline strings (real newlines inside string values)
- Missing colons between keys and values
- Missing closing quotes
- Non-standard numbers (`.75`, `0x1F`, `1_000`, `0042`, `Infinity`, `NaN`)
- Unclosed brackets/braces
- Unescaped control characters and backslashes

## Benchmark results

52/52 test cases pass (27 test + 25 validation).

| Tier | Cases | Avg Latency |
|---|---|---|
| Deterministic | 45 (87%) | <1ms |
| Deterministic + coerce | 2 (4%) | <1ms |
| Snippet LLM | 1 (2%) | ~2s |
| Full LLM | 4 (8%) | ~3-30s |

## Project structure

```
scripts/
  repair.py   — deterministic repair + schema validation + type coercion
  llm.py      — llama-server interaction (grammar, schema, snippet repair)
  server.py   — FastAPI app + 4-tier orchestration
tests/
  cases/      — 27 test cases with schemas
  validation/ — 25 validation cases (ProductCard, SupplierCard)
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
| Model RAM | ~1.4 GB |
| KV cache (during inference) | ~23 MB |
| Deterministic repair | <1ms |
| LLM repair | ~3-30s (4 vCPU) |
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

## Design decisions

- **Deterministic first** — regex/string parser handles ~98% of cases in <1ms, LLM is a fallback
- **No thinking/reasoning models** — pure autoregressive (Qwen2.5-Coder), prompt-in/JSON-out
- **llama-server over llama-cli** — model loads once, stays in RAM, each repair is an HTTP call
- **Grammar constraint is essential** — `json_schema` response format forces valid JSON at the token generation level

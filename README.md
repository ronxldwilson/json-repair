# JSON Repair

HTTP service that fixes broken JSON using a local LLM (Qwen2.5-Coder-1.5B) with grammar-constrained generation via llama.cpp. No external API calls — runs fully offline.

## How it works

1. You send broken JSON + a JSON Schema (or Pydantic `model_json_schema()` output)
2. The LLM repairs syntax errors (missing quotes, commas, trailing commas, `True` → `true`, etc.)
3. llama.cpp's grammar engine forces the output to conform to the schema — guaranteeing valid JSON with correct structure

The model stays loaded in RAM. Each repair is an HTTP call (~2-5 seconds on CPU).

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
  "valid": true
}
```

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

## CLI usage

The CLI tool is also included for local file repair:

```bash
python scripts/fix_json.py broken.json -v --schema schemas/test_expected.py:Person
python scripts/fix_json.py broken.json -i --schema schema.json  # in-place
python scripts/fix_json.py broken.json -v --stop-server          # stop llama-server after
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
| Repair latency | ~2s (Apple Silicon), ~5s (4 vCPU) |
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

- **No thinking/reasoning models** — pure autoregressive (Qwen2.5-Coder), prompt-in/JSON-out
- **No code-based heuristics** — LLM + grammar constraint handles all repairs, no regex post-processing
- **llama-server over llama-cli** — model loads once, stays in RAM, each repair is an HTTP call
- **Grammar constraint is essential** — `json_schema` response format forces valid JSON at the token generation level

"""JSON repair HTTP service — FastAPI app with 4-tier repair pipeline."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from scripts.repair import (
    coerce_schema_errors,
    deterministic_repair,
    resolve_refs,
    validate_against_schema,
)
from scripts.llm import (
    check_health,
    iterative_snippet_repair,
    repair_with_grammar,
    repair_with_schema,
    wait_for_server,
)

LLAMA_HOST = os.environ.get("LLAMA_HOST", "llama")
LLAMA_PORT = os.environ.get("LLAMA_PORT", "8776")
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
COMPLETIONS_URL = f"{LLAMA_URL}/v1/chat/completions"
COMPLETION_URL = f"{LLAMA_URL}/completion"
HEALTH_URL = f"{LLAMA_URL}/health"


@asynccontextmanager
async def lifespan(app: FastAPI):
    wait_for_server(HEALTH_URL)
    yield


app = FastAPI(title="JSON Repair Service", lifespan=lifespan)


class RepairRequest(BaseModel):
    broken_json: str
    schema_dict: dict | None = None


class RepairResponse(BaseModel):
    repaired_json: str
    valid: bool
    method: str = "llm"


@app.post("/repair", response_model=RepairResponse)
def repair(req: RepairRequest):
    schema = None
    if req.schema_dict:
        schema = resolve_refs(req.schema_dict)

    # 1. Deterministic repair
    deterministic_result = deterministic_repair(req.broken_json)
    if validate_against_schema(deterministic_result, schema):
        return RepairResponse(repaired_json=deterministic_result, valid=True, method="deterministic")

    # 2. Type coercion for schema mismatches
    if schema:
        coerced = coerce_schema_errors(deterministic_result, schema)
        if coerced and validate_against_schema(coerced, schema):
            return RepairResponse(repaired_json=coerced, valid=True, method="deterministic+coerce")

    # 3. Targeted snippet repair
    try:
        snippet_result, rounds = iterative_snippet_repair(COMPLETION_URL, deterministic_result)
        if rounds > 0 and validate_against_schema(snippet_result, schema):
            return RepairResponse(repaired_json=snippet_result, valid=True, method=f"snippet({rounds})")
    except Exception:
        pass

    # 4. Full LLM fallback
    try:
        if schema:
            repaired = repair_with_schema(COMPLETIONS_URL, req.broken_json, schema)
        else:
            repaired = repair_with_grammar(COMPLETION_URL, req.broken_json)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llama-server error: {e}")

    valid = validate_against_schema(repaired, schema)
    return RepairResponse(repaired_json=repaired, valid=valid, method="llm")


@app.get("/health")
def health():
    if check_health(HEALTH_URL):
        return {"status": "ok"}
    raise HTTPException(status_code=503, detail="llama-server not ready")

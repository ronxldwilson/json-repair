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
    configure,
    iterative_snippet_repair,
    repair_with_grammar,
    repair_with_schema,
    warmup_model,
    wait_for_server,
)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local")

LLAMA_HOST = os.environ.get("LLAMA_HOST", "llama")
LLAMA_PORT = os.environ.get("LLAMA_PORT", "8776")
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"

EXTERNAL_API_URL = os.environ.get("EXTERNAL_API_URL", "https://api.groq.com/openai/v1")
EXTERNAL_API_KEY = os.environ.get("EXTERNAL_API_KEY", "")
EXTERNAL_MODEL = os.environ.get("EXTERNAL_MODEL", "llama-3.3-70b-versatile")

HEALTH_URL = f"{LLAMA_URL}/health"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if LLM_PROVIDER == "external":
        configure(
            provider="external",
            completions_url=f"{EXTERNAL_API_URL}/chat/completions",
            api_key=EXTERNAL_API_KEY,
            model=EXTERNAL_MODEL,
            deterministic_repair_fn=deterministic_repair,
        )
    else:
        configure(
            provider="local",
            completions_url=f"{LLAMA_URL}/v1/chat/completions",
            completion_url=f"{LLAMA_URL}/completion",
            deterministic_repair_fn=deterministic_repair,
        )
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

    # 3. Targeted snippet repair (with deterministic cleanup after each round)
    try:
        snippet_result, rounds = iterative_snippet_repair(deterministic_result)
        if rounds > 0:
            snippet_result = deterministic_repair(snippet_result)
            if validate_against_schema(snippet_result, schema):
                return RepairResponse(repaired_json=snippet_result, valid=True, method=f"snippet({rounds})")
            if schema:
                coerced = coerce_schema_errors(snippet_result, schema)
                if coerced and validate_against_schema(coerced, schema):
                    return RepairResponse(repaired_json=coerced, valid=True, method=f"snippet({rounds})+coerce")
    except Exception:
        pass

    # 4. Full LLM fallback
    try:
        if schema:
            repaired = repair_with_schema(req.broken_json, schema)
        else:
            repaired = repair_with_grammar(req.broken_json)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llama-server error: {e}")

    valid = validate_against_schema(repaired, schema)
    return RepairResponse(repaired_json=repaired, valid=valid, method="llm")


@app.post("/warmup")
def warmup():
    if LLM_PROVIDER == "external":
        return {"status": "ok", "message": "external provider, no warmup needed"}
    if not check_health(HEALTH_URL):
        raise HTTPException(status_code=503, detail="llama-server not reachable")
    try:
        warmup_model()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"warmup inference failed: {e}")
    return {"status": "ok", "message": "model loaded and ready"}


@app.get("/health")
def health():
    if LLM_PROVIDER == "external":
        return {"status": "ok", "provider": "external", "model": EXTERNAL_MODEL}
    if check_health(HEALTH_URL):
        return {"status": "ok", "provider": "local"}
    raise HTTPException(status_code=503, detail="llama-server not ready")

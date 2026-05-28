#!/usr/bin/env python3
"""JSON repair HTTP service backed by llama-server."""

import json
import os
import time
from contextlib import asynccontextmanager
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LLAMA_HOST = os.environ.get("LLAMA_HOST", "llama")
LLAMA_PORT = os.environ.get("LLAMA_PORT", "8776")
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
COMPLETIONS_URL = f"{LLAMA_URL}/v1/chat/completions"
COMPLETION_URL = f"{LLAMA_URL}/completion"
HEALTH_URL = f"{LLAMA_URL}/health"

SYSTEM_PROMPT = (
    "You are a JSON repair tool. You receive broken JSON and return the corrected version. "
    "Preserve the original structure, keys, and values exactly. Only fix syntax errors "
    "(missing quotes, missing commas, trailing commas, wrong boolean literals, etc). "
    "Do not add, remove, rename, or rearrange any keys. Do not move values between objects. "
    "Return only the corrected JSON, nothing else."
)

JSON_GRAMMAR = r'''
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws

object ::=
  "{" ws (
            string ":" ws value
    ("," ws string ":" ws value)*
  )? "}" ws

array  ::=
  "[" ws (
            value
    ("," ws value)*
  )? "]" ws

string ::=
  "\"" (
    [^\\"\x7F\x00-\x1F] |
    "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
  )* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)? ws

ws ::= ([ \t\n] ws)?
'''


def wait_for_llama(timeout: int = 120):
    for _ in range(timeout):
        try:
            req = Request(HEALTH_URL)
            resp = urlopen(req, timeout=2)
            if resp.status == 200:
                return
        except (URLError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError(f"llama-server not reachable at {LLAMA_URL} after {timeout}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    wait_for_llama()
    yield


app = FastAPI(title="JSON Repair Service", lifespan=lifespan)


def resolve_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}
    if not defs:
        return schema

    def _resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]
                def_name = ref_path.rsplit("/", 1)[-1]
                if def_name in defs:
                    return _resolve(defs[def_name])
                return node
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


def repair_with_schema(broken_text: str, schema: dict) -> str:
    body = {
        "model": "local",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Fix this broken JSON:\n{broken_text}"},
        ],
        "temperature": 0,
        "max_tokens": 4096,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "repaired", "schema": schema},
        },
    }
    data = json.dumps(body).encode()
    req = Request(COMPLETIONS_URL, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=120)
    result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"].strip()


def repair_with_grammar(broken_text: str) -> str:
    prompt = (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\nFix this broken JSON:\n{broken_text}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    body = {
        "prompt": prompt,
        "temperature": 0,
        "n_predict": 4096,
        "grammar": JSON_GRAMMAR,
    }
    data = json.dumps(body).encode()
    req = Request(COMPLETION_URL, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=120)
    result = json.loads(resp.read())
    return result["content"].strip()


class RepairRequest(BaseModel):
    broken_json: str
    schema_dict: dict | None = None


class RepairResponse(BaseModel):
    repaired_json: str
    valid: bool


@app.post("/repair", response_model=RepairResponse)
def repair(req: RepairRequest):
    schema = None
    if req.schema_dict:
        schema = resolve_refs(req.schema_dict)

    try:
        if schema:
            repaired = repair_with_schema(req.broken_json, schema)
        else:
            repaired = repair_with_grammar(req.broken_json)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llama-server error: {e}")

    try:
        json.loads(repaired)
        valid = True
    except json.JSONDecodeError:
        valid = False

    return RepairResponse(repaired_json=repaired, valid=valid)


@app.get("/health")
def health():
    try:
        req = Request(HEALTH_URL)
        resp = urlopen(req, timeout=2)
        if resp.status == 200:
            return {"status": "ok"}
    except (URLError, OSError):
        pass
    raise HTTPException(status_code=503, detail="llama-server not ready")

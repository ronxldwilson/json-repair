#!/usr/bin/env python3
"""JSON repair HTTP service backed by llama-server."""

import json
import os
import re
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

SYSTEM_PROMPT = "Fix this JSON. Return only corrected JSON, no explanation."


def _replace_single_quotes(t: str) -> str:
    """Replace single-quoted strings with double-quoted strings."""
    result = []
    i = 0
    in_double = False
    while i < len(t):
        ch = t[i]
        if in_double:
            result.append(ch)
            if ch == '\\' and i + 1 < len(t):
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
        elif ch == '"':
            result.append(ch)
            in_double = True
        elif ch == "'":
            result.append('"')
            i += 1
            while i < len(t):
                c = t[i]
                if c == '\\' and i + 1 < len(t):
                    result.append(c)
                    result.append(t[i + 1])
                    i += 2
                    continue
                if c == "'":
                    result.append('"')
                    i += 1
                    break
                if c == '"':
                    result.append('\\"')
                else:
                    result.append(c)
                i += 1
            continue
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _fix_closing_quotes(t: str) -> str:
    """Fix lines where a string value is missing its closing quote."""
    lines = t.split('\n')
    fixed = []
    for line in lines:
        stripped = line.rstrip()
        m = re.match(r'^(\s*"[^"]*"\s*:\s*")(.*)$', stripped)
        if m:
            val_part = m.group(2)
            quote_count = len(re.findall(r'(?<!\\)"', val_part))
            if quote_count % 2 == 0:
                stripped = stripped + '"'
        fixed.append(stripped)
    return '\n'.join(fixed)


def _escape_control_chars(t: str) -> str:
    """Escape unescaped control characters inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(t):
        ch = t[i]
        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
        else:
            if ch == '\\' and i + 1 < len(t):
                result.append(ch)
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                result.append(ch)
                in_string = False
            elif ch == '\n' or ch == '\r' or ch == '\t':
                if ch == '\n':
                    result.append('\\n')
                elif ch == '\r':
                    result.append('\\r')
                elif ch == '\t':
                    result.append('\\t')
            elif ord(ch) < 0x20:
                result.append(f'\\u{ord(ch):04x}')
            else:
                result.append(ch)
        i += 1
    return ''.join(result)


def _strip_comments(t: str) -> str:
    """Remove // and /* */ comments outside of string values."""
    result = []
    i = 0
    in_string = False
    while i < len(t):
        ch = t[i]
        if in_string:
            result.append(ch)
            if ch == '\\' and i + 1 < len(t):
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            result.append(ch)
            in_string = True
        elif ch == '/' and i + 1 < len(t) and t[i + 1] == '/':
            while i < len(t) and t[i] != '\n':
                i += 1
            continue
        elif ch == '/' and i + 1 < len(t) and t[i + 1] == '*':
            i += 2
            while i + 1 < len(t) and not (t[i] == '*' and t[i + 1] == '/'):
                i += 1
            i += 2
            continue
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def deterministic_repair(text: str) -> str:
    """Fast regex/string-based repair for common JSON errors."""
    t = text.strip()

    # Strip markdown code fences
    t = re.sub(r'^```(?:json)?\s*\n?', '', t)
    t = re.sub(r'\n?```\s*$', '', t)

    # Strip preamble text before first { or [
    first_brace = min(
        (t.find('{') if '{' in t else len(t)),
        (t.find('[') if '[' in t else len(t)),
    )
    if first_brace > 0 and first_brace < len(t):
        t = t[first_brace:]

    # Strip trailing garbage after the last matching } or ]
    depth = 0
    end_pos = 0
    in_str = False
    for i, ch in enumerate(t):
        if in_str:
            if ch == '\\' and i + 1 < len(t):
                continue
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
            if depth == 0:
                end_pos = i + 1
                break
    if end_pos > 0 and end_pos < len(t):
        t = t[:end_pos]

    # Add missing outer braces
    t = t.strip()
    if t and t[0] != '{' and t[0] != '[':
        if '"' in t and ':' in t:
            t = '{' + t + '}'

    # Collapse multiple commas into one
    t = re.sub(r',\s*,+', ',', t)

    t = re.sub(r'\bTrue\b', 'true', t)
    t = re.sub(r'\bFalse\b', 'false', t)
    t = re.sub(r'\bNone\b', 'null', t)

    t = _fix_closing_quotes(t)

    if "'" in t:
        t = _replace_single_quotes(t)

    t = _strip_comments(t)

    t = re.sub(r'(?<=[{,\n])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r' "\1":', t)

    # Fix missing colon: "key" { or "key" [
    t = re.sub(r'(")\s+(\{)', r'\1: \2', t)
    t = re.sub(r'(")\s+(\[)', r'\1: \2', t)

    for _ in range(3):
        t = re.sub(r',(\s*[}\]])', r'\1', t)

    # Fix missing commas between values on separate lines
    t = re.sub(r'(")\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(\d)\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(true|false|null)\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', t)
    t = re.sub(r'(\])\s*\n(\s*\[)', r'\1,\n\2', t)
    t = re.sub(r'(\})\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(\])\s*\n(\s*")', r'\1,\n\2', t)

    t = re.sub(r'(")\s+(")', r'\1, \2', t)
    t = re.sub(r'(\d)\s+(")', r'\1, \2', t)
    t = re.sub(r'(true|false|null)\s+(")', r'\1, \2', t)

    # Escape control characters inside string values
    t = _escape_control_chars(t)

    # Fix unescaped backslashes (e.g. C:\Users → C:\\Users)
    t = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', t)

    return t

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
            {"role": "user", "content": broken_text},
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
        f"<|im_start|>user\n{broken_text}<|im_end|>\n"
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
    method: str = "llm"


def _validate_against_schema(json_str: str, schema: dict | None) -> bool:
    """Check if json_str is valid JSON and conforms to the schema."""
    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return False
    if not schema:
        return True
    try:
        from jsonschema import validate, ValidationError
        validate(parsed, schema)
        return True
    except ImportError:
        return True
    except (ValidationError, Exception):
        return False


@app.post("/repair", response_model=RepairResponse)
def repair(req: RepairRequest):
    schema = None
    if req.schema_dict:
        schema = resolve_refs(req.schema_dict)

    # Try deterministic repair first — must pass both JSON parse and schema validation
    deterministic_result = deterministic_repair(req.broken_json)
    if _validate_against_schema(deterministic_result, schema):
        return RepairResponse(repaired_json=deterministic_result, valid=True, method="deterministic")

    # Fall back to LLM
    try:
        if schema:
            repaired = repair_with_schema(req.broken_json, schema)
        else:
            repaired = repair_with_grammar(req.broken_json)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llama-server error: {e}")

    valid = _validate_against_schema(repaired, schema)
    return RepairResponse(repaired_json=repaired, valid=valid, method="llm")


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

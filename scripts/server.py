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


def _fix_multiline_strings(t: str) -> str:
    """Detect string values spanning multiple lines and escape embedded newlines."""
    lines = t.split('\n')
    result = []
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip()
        m = re.match(r'^(\s*"(?:[^"\\]|\\.)*"\s*:\s*")(.*)', stripped)
        if not m:
            result.append(lines[i])
            i += 1
            continue

        prefix = m.group(1)
        value_rest = m.group(2)
        uq = len(re.findall(r'(?<!\\)"', value_rest))
        if uq >= 1:
            result.append(lines[i])
            i += 1
            continue

        parts = [value_rest]
        i += 1
        while i < len(lines):
            cont = lines[i].rstrip()
            if re.match(r'^\s*"(?:[^"\\]|\\.)*"\s*:', cont):
                break
            if re.match(r'^\s*[}\]]+\s*,?\s*$', cont):
                break
            uq_cont = len(re.findall(r'(?<!\\)"', cont))
            if uq_cont >= 1:
                parts.append(cont)
                i += 1
                break
            parts.append(cont)
            i += 1

        result.append(prefix + '\\n'.join(parts))

    return '\n'.join(result)


def _fix_numbers(t: str) -> str:
    """Fix non-standard number formats: leading dots, underscores, hex, octal, Infinity."""
    t = re.sub(r'(?<![.\d])\.([\d])', r'0.\1', t)
    while True:
        new_t = re.sub(r'(\d)_(\d)', r'\1\2', t)
        if new_t == t:
            break
        t = new_t
    t = re.sub(r'\b0x([0-9a-fA-F]+)\b', lambda m: str(int(m.group(1), 16)), t)
    t = re.sub(r'(?<=:\s)0+(\d+)(?=[,\s}\]\n])', r'\1', t)
    t = re.sub(r'-?Infinity\b', 'null', t)
    t = re.sub(r'\bNaN\b', 'null', t)
    return t


def _auto_close_brackets(t: str) -> str:
    """Append missing closing brackets/braces."""
    stack = []
    in_str = False
    i = 0
    while i < len(t):
        ch = t[i]
        if in_str:
            if ch == '\\' and i + 1 < len(t):
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == '{':
            stack.append('}')
        elif ch == '[':
            stack.append(']')
        elif ch in '}]':
            if stack and stack[-1] == ch:
                stack.pop()
        i += 1
    return t + ''.join(reversed(stack))


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

    t = _fix_multiline_strings(t)
    t = _fix_closing_quotes(t)

    if "'" in t:
        t = _replace_single_quotes(t)

    t = _strip_comments(t)

    t = re.sub(r'(?<=[{,\n])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r' "\1":', t)

    # Fix missing colon: "key" "value", "key" 123, "key" true, "key" {, "key" [
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(")', r'\1: \2', t, flags=re.MULTILINE)
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(\d)', r'\1: \2', t, flags=re.MULTILINE)
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(true|false|null)', r'\1: \2', t, flags=re.MULTILINE)
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

    # Fix missing commas on same line
    t = re.sub(r'(")\s+(")', r'\1, \2', t)
    t = re.sub(r'(\])\s+(")', r'\1, \2', t)
    t = re.sub(r'(\])\s+(\[)', r'\1, \2', t)
    t = re.sub(r'(\})\s+(")', r'\1, \2', t)
    t = re.sub(r'(\})\s+(\{)', r'\1, \2', t)
    t = re.sub(r'(\d)\s+(")', r'\1, \2', t)
    t = re.sub(r'(true|false|null)\s+(")', r'\1, \2', t)

    t = _fix_numbers(t)

    # Escape control characters inside string values
    t = _escape_control_chars(t)

    # Fix unescaped backslashes (e.g. C:\Users → C:\\Users)
    t = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', t)

    t = _auto_close_brackets(t)

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


def _coerce_schema_errors(json_str: str, schema: dict) -> str | None:
    """Try to fix schema validation errors by coercing field types."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return None

    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(parsed, dict):
        return None

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(parsed))
    if not errors:
        return None

    changed = False
    for error in errors:
        path = list(error.absolute_path)

        # Navigate to the parent node and field
        if path:
            node = parsed
            for key in path[:-1]:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                elif isinstance(node, list) and isinstance(key, int) and key < len(node):
                    node = node[key]
                else:
                    node = None
                    break
            if node is None:
                continue
            field = path[-1]
        else:
            continue

        # Get the expected type from schema or anyOf variants
        expected_types = set()
        err_schema = error.schema
        if "type" in err_schema:
            expected_types.add(err_schema["type"])
        for variant in err_schema.get("anyOf", []):
            if "type" in variant:
                expected_types.add(variant["type"])

        if isinstance(node, dict) and field in node:
            val = node[field]

            # Dict with additionalProperties type constraint — coerce all values
            ap_type = None
            for s in [err_schema] + err_schema.get("anyOf", []):
                if s.get("type") == "object" and "additionalProperties" in s:
                    ap_type = s["additionalProperties"].get("type")
                    break
            if ap_type and isinstance(val, dict):
                for k, v in val.items():
                    if ap_type == "string" and not isinstance(v, str):
                        val[k] = str(v).lower() if isinstance(v, bool) else str(v)
                        changed = True
                continue

            if "string" in expected_types and not isinstance(val, str):
                node[field] = str(val).lower() if isinstance(val, bool) else str(val)
                changed = True
            elif "integer" in expected_types and isinstance(val, str):
                try:
                    node[field] = int(val)
                    changed = True
                except ValueError:
                    pass
            elif "number" in expected_types and isinstance(val, str):
                try:
                    node[field] = float(val)
                    changed = True
                except ValueError:
                    pass
            elif "boolean" in expected_types and isinstance(val, str):
                if val.lower() in ("true", "1", "yes"):
                    node[field] = True
                    changed = True
                elif val.lower() in ("false", "0", "no"):
                    node[field] = False
                    changed = True
            elif "array" in expected_types and not isinstance(val, list):
                node[field] = [val]
                changed = True

    if not changed:
        return None

    result = json.dumps(parsed, ensure_ascii=False)
    try:
        from jsonschema import validate, ValidationError
        validate(json.loads(result), schema)
        return result
    except Exception:
        return None


SNIPPET_PROMPT = "Fix the JSON syntax error in this snippet. Return ONLY the fixed snippet, nothing else."
WINDOW = 200


def _repair_snippet_via_llm(text: str, error: json.JSONDecodeError) -> str:
    """Ask the LLM to fix a small window around the JSON parse error."""
    pos = error.pos
    start = max(0, pos - WINDOW)
    end = min(len(text), pos + WINDOW)
    snippet = text[start:end]

    prompt = (
        f"<|im_start|>system\n{SNIPPET_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"Error: {error.msg} at position {pos - start}\n"
        f"Snippet:\n{snippet}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    body = {
        "prompt": prompt,
        "temperature": 0,
        "n_predict": WINDOW * 2,
    }
    data = json.dumps(body).encode()
    req = Request(COMPLETION_URL, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=30)
    result = json.loads(resp.read())
    fixed_snippet = result["content"].strip()

    # Strip markdown fences if the LLM wrapped the output
    fixed_snippet = re.sub(r'^```(?:json)?\s*\n?', '', fixed_snippet)
    fixed_snippet = re.sub(r'\n?```\s*$', '', fixed_snippet)

    return text[:start] + fixed_snippet + text[end:]


def _iterative_snippet_repair(text: str, max_rounds: int = 5) -> tuple[str, int]:
    """Repeatedly fix JSON errors one snippet at a time. Returns (result, rounds_used)."""
    for round_num in range(1, max_rounds + 1):
        try:
            json.loads(text)
            return text, round_num - 1
        except json.JSONDecodeError as e:
            text = _repair_snippet_via_llm(text, e)

    return text, max_rounds


@app.post("/repair", response_model=RepairResponse)
def repair(req: RepairRequest):
    schema = None
    if req.schema_dict:
        schema = resolve_refs(req.schema_dict)

    # 1. Deterministic repair
    deterministic_result = deterministic_repair(req.broken_json)
    if _validate_against_schema(deterministic_result, schema):
        return RepairResponse(repaired_json=deterministic_result, valid=True, method="deterministic")

    # 2. Type coercion for schema mismatches
    if schema:
        coerced = _coerce_schema_errors(deterministic_result, schema)
        if coerced and _validate_against_schema(coerced, schema):
            return RepairResponse(repaired_json=coerced, valid=True, method="deterministic+coerce")

    # 3. Targeted snippet repair — fix errors one at a time with small LLM calls
    try:
        snippet_result, rounds = _iterative_snippet_repair(deterministic_result)
        if rounds > 0 and _validate_against_schema(snippet_result, schema):
            return RepairResponse(repaired_json=snippet_result, valid=True, method=f"snippet({rounds})")
    except Exception:
        pass

    # 4. Full LLM fallback — regenerate entire JSON with schema constraint
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

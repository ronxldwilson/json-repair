#!/usr/bin/env python3
"""JSON repair tool using a local LLM via llama-server."""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "models" / "qwen2.5-coder-1.5b-instruct-q5_k_m.gguf"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8776
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
HEALTH_URL = f"{SERVER_URL}/health"
COMPLETIONS_URL = f"{SERVER_URL}/v1/chat/completions"
COMPLETION_URL = f"{SERVER_URL}/completion"

SYSTEM_PROMPT = "Fix this JSON. Return only corrected JSON, no explanation."


def _replace_single_quotes(t: str) -> str:
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


def _strip_comments(t: str) -> str:
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


def _escape_control_chars(t: str) -> str:
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
            elif ch == '\n':
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

    t = re.sub(r'^```(?:json)?\s*\n?', '', t)
    t = re.sub(r'\n?```\s*$', '', t)

    first_brace = min(
        (t.find('{') if '{' in t else len(t)),
        (t.find('[') if '[' in t else len(t)),
    )
    if first_brace > 0 and first_brace < len(t):
        t = t[first_brace:]

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

    t = t.strip()
    if t and t[0] != '{' and t[0] != '[':
        if '"' in t and ':' in t:
            t = '{' + t + '}'

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

    t = _escape_control_chars(t)
    t = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', t)

    t = _auto_close_brackets(t)

    return t

# GBNF grammar that forces valid strict JSON output
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

_server_process = None


def start_server():
    global _server_process
    if is_server_running():
        return

    if not MODEL_PATH.exists():
        print(f"Model not found: {MODEL_PATH}", file=sys.stderr)
        print("Download it with:", file=sys.stderr)
        print(f"  python3 -c \"from huggingface_hub import hf_hub_download; hf_hub_download('Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF', '{MODEL_PATH.name}', local_dir='./models')\"", file=sys.stderr)
        sys.exit(1)

    threads = min(4, os.cpu_count() or 4)
    cmd = [
        "llama-server",
        "-m", str(MODEL_PATH),
        "--host", SERVER_HOST,
        "--port", str(SERVER_PORT),
        "-t", str(threads),
        "-c", "4096",
        "--temp", "0",
    ]
    _server_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(30):
        time.sleep(1)
        if is_server_running():
            return
    print("Server failed to start within 30s", file=sys.stderr)
    sys.exit(1)


def is_server_running():
    try:
        req = Request(HEALTH_URL)
        resp = urlopen(req, timeout=2)
        return resp.status == 200
    except (URLError, OSError):
        return False


def stop_server():
    global _server_process
    if _server_process:
        _server_process.terminate()
        _server_process.wait(timeout=10)
        _server_process = None


def repair_with_schema(broken_text: str, schema: dict) -> str:
    """Use /v1/chat/completions with json_schema response_format."""
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
    """Use /completion with GBNF grammar for generic JSON constraint."""
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


def repair_json(broken_text: str, schema: dict | None = None) -> tuple[str, str]:
    """Returns (repaired_text, method). Tries deterministic first, LLM fallback."""
    deterministic_result = deterministic_repair(broken_text)
    try:
        json.loads(deterministic_result)
        return deterministic_result, "deterministic"
    except json.JSONDecodeError:
        pass

    if schema:
        return repair_with_schema(broken_text, schema), "llm"
    return repair_with_grammar(broken_text), "llm"


def validate_json(text: str, schema: dict | None = None) -> tuple[bool, str]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    if schema:
        try:
            import jsonschema
            jsonschema.validate(parsed, schema)
        except ImportError:
            return True, "Valid JSON (schema validation skipped — pip install jsonschema)"
        except jsonschema.ValidationError as e:
            return False, f"Schema validation failed: {e.message}"

    return True, "Valid JSON"


def resolve_refs(schema: dict) -> dict:
    """Inline all $ref references so llama-server doesn't need to resolve them."""
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}
    if not defs:
        return schema

    def _resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]  # e.g. "#/$defs/Address"
                def_name = ref_path.rsplit("/", 1)[-1]
                if def_name in defs:
                    return _resolve(defs[def_name])
                return node
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


def load_schema(schema_arg: str) -> dict:
    """Load schema from a .json file or a Pydantic module (path.py:ClassName)."""
    if ":" in schema_arg and schema_arg.rsplit(":", 1)[0].endswith(".py"):
        module_path, class_name = schema_arg.rsplit(":", 1)
        spec = importlib.util.spec_from_file_location("_schema", Path(module_path).resolve())
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, class_name)
        return resolve_refs(cls.model_json_schema())

    if schema_arg.endswith(".py"):
        print("Ambiguous: multiple models may exist. Use --schema path.py:ClassName", file=sys.stderr)
        sys.exit(1)

    return resolve_refs(json.loads(Path(schema_arg).read_text()))


def main():
    parser = argparse.ArgumentParser(description="Repair broken JSON files using a local LLM")
    parser.add_argument("files", nargs="+", help="JSON files to repair")
    parser.add_argument("--in-place", "-i", action="store_true", help="Overwrite input files with repaired output")
    parser.add_argument("--validate", "-v", action="store_true", help="Validate output JSON")
    parser.add_argument("--schema", "-s", help="JSON Schema file (.json) or Pydantic module (.py) e.g. schemas/test_expected.py:Person")
    parser.add_argument("--stop-server", action="store_true", help="Stop the llama-server after processing")
    parser.add_argument("--output-dir", "-o", help="Write repaired files to this directory")
    args = parser.parse_args()

    schema = None
    if args.schema:
        schema = load_schema(args.schema)

    start_server()

    try:
        for filepath in args.files:
            path = Path(filepath)
            if not path.exists():
                print(f"File not found: {path}", file=sys.stderr)
                continue

            broken = path.read_text()
            print(f"Repairing {path}...")
            repaired, method = repair_json(broken, schema)
            print(f"  Method: {method}")

            if args.validate:
                valid, msg = validate_json(repaired, schema)
                status = "OK" if valid else "FAIL"
                print(f"  [{status}] {msg}")

            if args.in_place:
                path.write_text(repaired + "\n")
                print(f"  Written to {path}")
            elif args.output_dir:
                out = Path(args.output_dir) / path.name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(repaired + "\n")
                print(f"  Written to {out}")
            else:
                print(repaired)
    finally:
        if args.stop_server:
            stop_server()


if __name__ == "__main__":
    main()

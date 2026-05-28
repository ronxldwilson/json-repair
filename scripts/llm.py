"""LLM-based JSON repair via llama-server."""

import json
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

SYSTEM_PROMPT = "Fix this JSON. Return only corrected JSON, no explanation."
SNIPPET_PROMPT = "Fix the JSON syntax error in this snippet. Return ONLY the fixed snippet, nothing else."
SNIPPET_WINDOW = 200

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


def wait_for_server(health_url: str, timeout: int = 120):
    """Poll llama-server health endpoint until it responds 200, or raise after timeout.

    Called at startup to block the API service until the model is loaded and ready.
    Retries once per second for up to `timeout` seconds.

    Example:
        wait_for_server("http://llama:8776/health", timeout=120)
        # blocks until server is ready, then returns None
        # raises RuntimeError if not reachable after 120s
    """
    for _ in range(timeout):
        try:
            req = Request(health_url)
            resp = urlopen(req, timeout=2)
            if resp.status == 200:
                return
        except (URLError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError(f"llama-server not reachable at {health_url} after {timeout}s")


def check_health(health_url: str) -> bool:
    """Single health check — returns True if llama-server responds 200, False otherwise.

    Used by the /health endpoint to report whether the LLM backend is available.

    Example:
        check_health("http://llama:8776/health")  # → True if server is up
    """
    try:
        req = Request(health_url)
        resp = urlopen(req, timeout=2)
        return resp.status == 200
    except (URLError, OSError):
        return False


def repair_with_schema(completions_url: str, broken_text: str, schema: dict) -> str:
    """Full LLM repair using json_schema response format to guarantee structure.

    Sends the broken JSON to llama-server's /v1/chat/completions endpoint with
    a json_schema constraint. The model regenerates the entire JSON from scratch,
    and the grammar constraint ensures the output matches the provided schema
    at the token generation level — every token is forced to be schema-valid.

    This is the most expensive tier (~3-30s) but handles cases where the JSON
    is too corrupted for deterministic or snippet repair.

    Example:
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        repair_with_schema(url, '{"name: John}', schema)
        # → '{"name": "John"}'
    """
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
    req = Request(completions_url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=120)
    result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"].strip()


def repair_with_grammar(completion_url: str, broken_text: str) -> str:
    """LLM repair using GBNF grammar constraint (no schema, just valid JSON syntax).

    Uses llama-server's /completion endpoint (not chat) with a GBNF grammar that
    enforces valid JSON structure. The prompt is formatted in ChatML template
    (<|im_start|>/<|im_end|> tags) since we're bypassing the chat API.

    Used as a fallback when no schema is provided — guarantees syntactically valid
    JSON but can't enforce a specific structure (nested objects may get rearranged).

    Example:
        repair_with_grammar(url, '{"name: "John", age: 30}')
        # → '{"name": "John", "age": 30}'
    """
    # ChatML template: system/user/assistant turns for the raw /completion endpoint
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
    req = Request(completion_url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=120)
    result = json.loads(resp.read())
    return result["content"].strip()


def repair_snippet(completion_url: str, text: str, error: json.JSONDecodeError) -> str:
    """Targeted snippet repair — sends only a 200-char window around the error to the LLM.

    Instead of sending the entire (potentially huge) JSON to the model, this extracts
    a ±200 character window centered on the parse error position. The LLM fixes just
    that snippet, which is then spliced back into the original text.

    Much faster than full LLM repair (~0.8s vs ~3-30s) because the model only
    processes and generates a small amount of text. No grammar constraint is used
    since the snippet may not be a complete JSON structure.

    Example:
        text = '{"name": "John", "age": 30, "items": [1 2 3]}'
        error = json.JSONDecodeError("Expecting ',' delimiter", text, 39)
        repair_snippet(url, text, error)
        # → '{"name": "John", "age": 30, "items": [1, 2, 3]}'
    """
    pos = error.pos
    # extract ±SNIPPET_WINDOW chars around the error position
    start = max(0, pos - SNIPPET_WINDOW)
    end = min(len(text), pos + SNIPPET_WINDOW)
    snippet = text[start:end]

    # ChatML prompt with the error message and snippet context
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
        "n_predict": SNIPPET_WINDOW * 2,
    }
    data = json.dumps(body).encode()
    req = Request(completion_url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=30)
    result = json.loads(resp.read())
    fixed_snippet = result["content"].strip()

    # strip markdown fences the LLM sometimes wraps around its response
    fixed_snippet = re.sub(r'^```(?:json)?\s*\n?', '', fixed_snippet)  # opening fence: ```json
    fixed_snippet = re.sub(r'\n?```\s*$', '', fixed_snippet)  # closing fence: ```

    # splice the fixed snippet back into the original text at the same position
    return text[:start] + fixed_snippet + text[end:]


def iterative_snippet_repair(completion_url: str, text: str, max_rounds: int = 5) -> tuple[str, int]:
    """Run snippet repair in a loop until the JSON parses or max_rounds is reached.

    Each round: try json.loads → if it fails, call repair_snippet on the error.
    Returns a tuple of (repaired_text, rounds_used). If the JSON was already valid,
    rounds_used is 0. Most cases resolve in 1-2 rounds.

    Example:
        text = '{"a": 1 "b": 2 "c": [3 4]}'  # missing commas in two places
        fixed, rounds = iterative_snippet_repair(url, text, max_rounds=5)
        # round 1 fixes "1 "b" → "1, "b", round 2 fixes "3 4" → "3, 4"
        # → ('{"a": 1, "b": 2, "c": [3, 4]}', 2)
    """
    for round_num in range(1, max_rounds + 1):
        try:
            json.loads(text)
            return text, round_num - 1
        except json.JSONDecodeError as e:
            text = repair_snippet(completion_url, text, e)

    return text, max_rounds

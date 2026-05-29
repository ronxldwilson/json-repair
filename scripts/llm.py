"""LLM-based JSON repair via llama-server or external OpenAI-compatible API (Groq, etc.)."""

import json
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

SYSTEM_PROMPT = "Fix this JSON. Return only corrected JSON, no explanation."
SNIPPET_PROMPT = "Fix the JSON syntax error in this snippet. Return ONLY the fixed snippet, nothing else."
SNIPPET_WINDOW = 200

# module-level config — set via configure()
_provider = "local"
_completions_url = None
_completion_url = None
_api_key = None
_model = None
_deterministic_repair = None


def configure(provider: str, completions_url: str, completion_url: str | None = None,
              api_key: str | None = None, model: str | None = None,
              deterministic_repair_fn=None):
    """Set the LLM backend config. Called once at startup from server.py.

    provider="local"    → uses llama-server (completion + chat/completions endpoints)
    provider="external" → uses any OpenAI-compatible API (Groq, OpenAI, Together, etc.)

    Example:
        configure("external",
                  completions_url="https://api.groq.com/openai/v1/chat/completions",
                  api_key="gsk_...", model="llama-3.3-70b-versatile")
    """
    global _provider, _completions_url, _completion_url, _api_key, _model, _deterministic_repair
    _provider = provider
    _completions_url = completions_url
    _completion_url = completion_url
    _api_key = api_key
    _model = model
    _deterministic_repair = deterministic_repair_fn


def _post(url: str, body: dict, timeout: int = 120) -> dict:
    """POST JSON to a URL, adding Bearer auth if _api_key is set."""
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "json-repair/1.0"}
    if _api_key:
        headers["Authorization"] = f"Bearer {_api_key}"
    req = Request(url, data=data, headers=headers)
    resp = urlopen(req, timeout=timeout)
    return json.loads(resp.read())

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


def repair_with_schema(broken_text: str, schema: dict) -> str:
    """Full LLM repair using json_schema response format to guarantee structure.

    Sends the broken JSON to the chat/completions endpoint with a json_schema
    constraint. The model regenerates the entire JSON from scratch, and the
    grammar constraint ensures the output matches the provided schema.

    Works with both local llama-server and external APIs (Groq, OpenAI, etc.).
    External APIs that don't support json_schema fall back to json_object mode
    with the schema embedded in the system prompt.

    Example:
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        repair_with_schema('{"name: John}', schema)
        # → '{"name": "John"}'
    """
    body = {
        "model": _model or "local",
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
    try:
        result = _post(_completions_url, body)
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        if _provider == "local":
            raise
        # fallback: some external APIs don't support json_schema, use json_object + schema in prompt
        body["messages"][0]["content"] = (
            f"{SYSTEM_PROMPT}\nOutput must match this JSON schema:\n{json.dumps(schema)}"
        )
        body["response_format"] = {"type": "json_object"}
        result = _post(_completions_url, body)
        return result["choices"][0]["message"]["content"].strip()


def repair_with_grammar(broken_text: str) -> str:
    """LLM repair with no schema — uses GBNF grammar (local) or json_object mode (external).

    Local: uses llama-server's /completion endpoint with a GBNF grammar that forces
    valid JSON at the token level. Prompt is ChatML-formatted.
    External: uses chat/completions with response_format=json_object.

    Used as a fallback when no schema is provided — guarantees syntactically valid
    JSON but can't enforce a specific structure.

    Example:
        repair_with_grammar('{"name: "John", age: 30}')
        # → '{"name": "John", "age": 30}'
    """
    if _provider == "external":
        body = {
            "model": _model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": broken_text},
            ],
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        result = _post(_completions_url, body)
        return result["choices"][0]["message"]["content"].strip()

    # local llama-server: use raw /completion with GBNF grammar + ChatML template
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
    result = _post(_completion_url, body)
    return result["content"].strip()


def repair_snippet(text: str, error: json.JSONDecodeError) -> str:
    """Targeted snippet repair — sends only a 200-char window around the error to the LLM.

    Instead of sending the entire (potentially huge) JSON to the model, this extracts
    a ±200 character window centered on the parse error position. The LLM fixes just
    that snippet, which is then spliced back into the original text.

    Works with both local (raw /completion) and external (chat/completions) APIs.

    Example:
        text = '{"name": "John", "age": 30, "items": [1 2 3]}'
        error = json.JSONDecodeError("Expecting ',' delimiter", text, 39)
        repair_snippet(text, error)
        # → '{"name": "John", "age": 30, "items": [1, 2, 3]}'
    """
    pos = error.pos
    # extract ±SNIPPET_WINDOW chars around the error position
    start = max(0, pos - SNIPPET_WINDOW)
    end = min(len(text), pos + SNIPPET_WINDOW)
    snippet = text[start:end]

    user_content = f"Error: {error.msg} at position {pos - start}\nSnippet:\n{snippet}"

    if _provider == "external":
        body = {
            "model": _model,
            "messages": [
                {"role": "system", "content": SNIPPET_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "max_tokens": SNIPPET_WINDOW * 2,
        }
        result = _post(_completions_url, body, timeout=30)
        fixed_snippet = result["choices"][0]["message"]["content"].strip()
    else:
        # local llama-server: use raw /completion with ChatML template
        prompt = (
            f"<|im_start|>system\n{SNIPPET_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{user_content}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        body = {
            "prompt": prompt,
            "temperature": 0,
            "n_predict": SNIPPET_WINDOW * 2,
        }
        result = _post(_completion_url, body, timeout=30)
        fixed_snippet = result["content"].strip()

    # strip markdown fences the LLM sometimes wraps around its response
    fixed_snippet = re.sub(r'^```(?:json)?\s*\n?', '', fixed_snippet)  # opening fence: ```json
    fixed_snippet = re.sub(r'\n?```\s*$', '', fixed_snippet)  # closing fence: ```

    # splice the fixed snippet back into the original text at the same position
    return text[:start] + fixed_snippet + text[end:]


def iterative_snippet_repair(text: str, max_rounds: int = 5) -> tuple[str, int]:
    """Run snippet repair in a loop until the JSON parses or max_rounds is reached.

    Each round: try json.loads → if it fails, call repair_snippet on the error.
    Returns a tuple of (repaired_text, rounds_used). If the JSON was already valid,
    rounds_used is 0. Most cases resolve in 1-2 rounds.

    Example:
        text = '{"a": 1 "b": 2 "c": [3 4]}'  # missing commas in two places
        fixed, rounds = iterative_snippet_repair(text, max_rounds=5)
        # round 1 fixes "1 "b" → "1, "b", round 2 fixes "3 4" → "3, 4"
        # → ('{"a": 1, "b": 2, "c": [3, 4]}', 2)
    """
    for round_num in range(1, max_rounds + 1):
        try:
            json.loads(text)
            return text, round_num - 1
        except json.JSONDecodeError as e:
            text = repair_snippet(text, e)
            if _deterministic_repair:
                text = _deterministic_repair(text)

    return text, max_rounds

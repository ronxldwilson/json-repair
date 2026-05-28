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
    try:
        req = Request(health_url)
        resp = urlopen(req, timeout=2)
        return resp.status == 200
    except (URLError, OSError):
        return False


def repair_with_schema(completions_url: str, broken_text: str, schema: dict) -> str:
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
    pos = error.pos
    start = max(0, pos - SNIPPET_WINDOW)
    end = min(len(text), pos + SNIPPET_WINDOW)
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
        "n_predict": SNIPPET_WINDOW * 2,
    }
    data = json.dumps(body).encode()
    req = Request(completion_url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=30)
    result = json.loads(resp.read())
    fixed_snippet = result["content"].strip()

    fixed_snippet = re.sub(r'^```(?:json)?\s*\n?', '', fixed_snippet)
    fixed_snippet = re.sub(r'\n?```\s*$', '', fixed_snippet)

    return text[:start] + fixed_snippet + text[end:]


def iterative_snippet_repair(completion_url: str, text: str, max_rounds: int = 5) -> tuple[str, int]:
    for round_num in range(1, max_rounds + 1):
        try:
            json.loads(text)
            return text, round_num - 1
        except json.JSONDecodeError as e:
            text = repair_snippet(completion_url, text, e)

    return text, max_rounds

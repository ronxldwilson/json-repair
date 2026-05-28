"""Deterministic JSON repair — regex/string-based fixes for common errors."""

import json
import re


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


def _fix_multiline_strings(t: str) -> str:
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


def _quote_leading_zero_numbers(t: str) -> str:
    pat = re.compile(r'(?<![.\d\w"])0(\d[\d.eE+\-]*)')
    result = []
    in_str = False
    i = 0
    while i < len(t):
        ch = t[i]
        if in_str:
            result.append(ch)
            if ch == '\\' and i + 1 < len(t):
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            result.append(ch)
            i += 1
            continue
        m = pat.match(t, i)
        if m and i == m.start():
            full = m.group(0)
            result.append('"')
            result.append(full)
            result.append('"')
            i = m.end()
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _fix_numbers(t: str) -> str:
    t = re.sub(r'(?<![.\d])\.([\d])', r'0.\1', t)
    t = re.sub(r'(\d)\.(?=[,\s\]\}\)]|$)', r'\g<1>.0', t)
    while True:
        new_t = re.sub(r'(\d)_(\d)', r'\1\2', t)
        if new_t == t:
            break
        t = new_t
    t = re.sub(r'\b0x([0-9a-fA-F]+)\b', lambda m: str(int(m.group(1), 16)), t)
    t = re.sub(r'(?<=:\s)0+(\d+)(?=[,\s}\]\n])', r'\1', t)
    t = _quote_leading_zero_numbers(t)
    t = re.sub(r'-?Infinity\b', 'null', t)
    t = re.sub(r'\bNaN\b', 'null', t)
    return t


def _strip_ellipsis(t: str) -> str:
    result = []
    in_str = False
    i = 0
    while i < len(t):
        ch = t[i]
        if in_str:
            result.append(ch)
            if ch == '\\' and i + 1 < len(t):
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            result.append(ch)
            in_str = True
        elif ch == '.' and i + 2 < len(t) and t[i + 1] == '.' and t[i + 2] == '.':
            i += 3
            continue
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _close_truncated_strings(t: str) -> str:
    in_str = False
    i = 0
    while i < len(t):
        ch = t[i]
        if not in_str:
            if ch == '"':
                in_str = True
        else:
            if ch == '\\' and i + 1 < len(t):
                i += 2
                continue
            if ch == '"':
                in_str = False
        i += 1
    if in_str:
        j = len(t) - 1
        trailing_bs = 0
        while j >= 0 and t[j] == '\\':
            trailing_bs += 1
            j -= 1
        if trailing_bs % 2 == 1:
            t = t[:-1]
        t = t + '"'
    return t


def _strip_bare_escapes(t: str) -> str:
    result = []
    in_str = False
    i = 0
    while i < len(t):
        ch = t[i]
        if in_str:
            result.append(ch)
            if ch == '\\' and i + 1 < len(t):
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            result.append(ch)
            in_str = True
        elif ch == '\\' and i + 1 < len(t):
            i += 2
            continue
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _auto_close_brackets(t: str) -> str:
    result = []
    stack = []
    in_str = False
    i = 0
    while i < len(t):
        ch = t[i]
        if in_str:
            if ch == '\\' and i + 1 < len(t):
                result.append(ch)
                result.append(t[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            result.append(ch)
        elif ch == '"':
            in_str = True
            result.append(ch)
        elif ch == '{':
            stack.append('}')
            result.append(ch)
        elif ch == '[':
            stack.append(']')
            result.append(ch)
        elif ch in '}]':
            if stack:
                expected = stack.pop()
                result.append(expected)
            else:
                result.append(ch)
        else:
            result.append(ch)
        i += 1
    return ''.join(result) + ''.join(reversed(stack))


def deterministic_repair(text: str) -> str:
    """Fast regex/string-based repair for common JSON errors."""
    t = text.strip()

    t = re.sub(r'^```(?:json)?\s*\n?', '', t)
    t = re.sub(r'\n?```\s*$', '', t)

    m = re.match(r'^[a-zA-Z_]\w*\s*\((.*)\)\s*;?\s*$', t, re.DOTALL)
    if m and '{' not in t and '[' not in t:
        t = m.group(1).strip()

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
    t = re.sub(r'\bundefined\b', 'null', t)

    t = re.sub(r'\b[A-Z][a-zA-Z]*\("([^"]*)"\)', r'"\1"', t)

    t = _fix_multiline_strings(t)
    t = _fix_closing_quotes(t)

    if "'" in t:
        t = _replace_single_quotes(t)

    t = _strip_comments(t)

    if '...' in t:
        t = _strip_ellipsis(t)
        t = re.sub(r',\s*,+', ',', t)

    t = re.sub(r'(?<=[{,\n])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r' "\1":', t)

    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(")', r'\1: \2', t, flags=re.MULTILINE)
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(\d)', r'\1: \2', t, flags=re.MULTILINE)
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(true|false|null)', r'\1: \2', t, flags=re.MULTILINE)
    t = re.sub(r'(?<=[{,])\s*("(?:[^"\\]|\\.)*")\s+(")', r' \1: \2', t)
    t = re.sub(r'(?<=[{,])\s*("(?:[^"\\]|\\.)*")\s+(\d)', r' \1: \2', t)
    t = re.sub(r'(?<=[{,])\s*("(?:[^"\\]|\\.)*")\s+(true|false|null)', r' \1: \2', t)
    t = re.sub(r'(")\s+(\{)', r'\1: \2', t)
    t = re.sub(r'(")\s+(\[)', r'\1: \2', t)

    t = re.sub(r'(:\s*)([\}\]])', r'\1null\2', t)
    t = re.sub(r'(:\s*)(,)', r'\1null\2', t)

    t = re.sub(r'(")\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(\d)\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(true|false|null)\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', t)
    t = re.sub(r'(\])\s*\n(\s*\[)', r'\1,\n\2', t)
    t = re.sub(r'(\})\s*\n(\s*")', r'\1,\n\2', t)
    t = re.sub(r'(\])\s*\n(\s*")', r'\1,\n\2', t)

    t = re.sub(r'(")\s+(")', r'\1, \2', t)
    t = re.sub(r'(\])\s+(")', r'\1, \2', t)
    t = re.sub(r'(\])\s+(\[)', r'\1, \2', t)
    t = re.sub(r'(\})\s+(")', r'\1, \2', t)
    t = re.sub(r'(\})\s+(\{)', r'\1, \2', t)
    t = re.sub(r'(\d)\s+(")', r'\1, \2', t)
    t = re.sub(r'(true|false|null)\s+(")', r'\1, \2', t)

    t = re.sub(r'(\d[eE][+\-]?)(?=[,\s\]\}]|$)', r'\g<1>0', t)
    t = re.sub(r'-(?=[,\s\]\}]|$)', '-0', t)

    t = _fix_numbers(t)
    t = _escape_control_chars(t)
    t = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', t)

    t = _strip_bare_escapes(t)
    t = _close_truncated_strings(t)
    t = _auto_close_brackets(t)

    t = re.sub(r'(:\s*)([\}\]])', r'\1null\2', t)
    t = re.sub(r'^(\s*\{\s*)("(?:[^"\\]|\\.)*")(\s*\}\s*)$', r'\1\2:null\3', t)

    for _ in range(3):
        t = re.sub(r',(\s*[}\]])', r'\1', t)

    t = re.sub(r'([\[{])(\s*),(\s*)', r'\1\2\3', t)

    return t


def resolve_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}
    if not defs:
        return schema

    def _resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                def_name = node["$ref"].rsplit("/", 1)[-1]
                if def_name in defs:
                    return _resolve(defs[def_name])
                return node
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


def validate_against_schema(json_str: str, schema: dict | None) -> bool:
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


def coerce_schema_errors(json_str: str, schema: dict) -> str | None:
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

        expected_types = set()
        err_schema = error.schema
        if "type" in err_schema:
            expected_types.add(err_schema["type"])
        for variant in err_schema.get("anyOf", []):
            if "type" in variant:
                expected_types.add(variant["type"])

        if isinstance(node, dict) and field in node:
            val = node[field]

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

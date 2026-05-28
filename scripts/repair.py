"""Deterministic JSON repair — regex/string-based fixes for common errors."""

import json
import re


def _replace_single_quotes(t: str) -> str:
    """Convert single-quoted strings to double-quoted, including escaped single quotes.

    Examples:
        {'key': 'value'}    → {"key": "value"}
        {"a":\\'foo\\'}       → {"a":"foo"}
        'India\\'s best'    → "India's best"
    """
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
        elif ch == '\\' and i + 1 < len(t) and t[i + 1] == "'":
            result.append('"')
            i += 2
            while i < len(t):
                c = t[i]
                if c == '\\' and i + 1 < len(t) and t[i + 1] == "'":
                    result.append('"')
                    i += 2
                    break
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
        elif ch == "'":
            result.append('"')
            i += 1
            while i < len(t):
                c = t[i]
                if c == '\\' and i + 1 < len(t) and t[i + 1] == "'":
                    result.append(t[i + 1])
                    i += 2
                    continue
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
    """Add missing closing quotes on string values at end of line.

    Examples:
        "key": "value   → "key": "value"
        "name": "John   → "name": "John"
    """
    lines = t.split('\n')
    fixed = []
    for line in lines:
        stripped = line.rstrip()
        m = re.match(r'^(\s*"[^"]*"\s*:\s*")(.*)$', stripped)  # match "key": "value_start
        if m:
            val_part = m.group(2)
            quote_count = len(re.findall(r'(?<!\\)"', val_part))  # count unescaped quotes in value
            if quote_count % 2 == 0:
                stripped = stripped + '"'
        fixed.append(stripped)
    return '\n'.join(fixed)


def _escape_control_chars(t: str) -> str:
    """Escape raw control characters inside JSON strings (newlines, tabs, etc).

    Examples:
        {"key": "line1\\nline2"}  → {"key": "line1\\\\nline2"}  (real newline → \\n)
        {"key": "tab\\there"}     → {"key": "tab\\\\there"}      (real tab → \\t)
    """
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
    """Remove // line comments and /* block comments */ outside of strings.

    Examples:
        {"a": 1} // comment       → {"a": 1}
        {"a": /* inline */ 1}      → {"a":  1}
        {"a": "has // inside"}     → {"a": "has // inside"}  (strings untouched)
    """
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
    """Join multiline string values into single-line with \\n escapes.

    Detects string values that span multiple lines (missing closing quote) and
    joins continuation lines with literal \\n until a closing quote or next key is found.

    Example:
        "desc": "line one        → "desc": "line one\\nline two"
        line two"
    """
    lines = t.split('\n')
    result = []
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip()
        m = re.match(r'^(\s*"(?:[^"\\]|\\.)*"\s*:\s*")(.*)', stripped)  # match "key": "value_start
        if not m:
            result.append(lines[i])
            i += 1
            continue

        prefix = m.group(1)
        value_rest = m.group(2)
        uq = len(re.findall(r'(?<!\\)"', value_rest))  # count unescaped quotes
        if uq >= 1:
            result.append(lines[i])
            i += 1
            continue

        parts = [value_rest]
        i += 1
        while i < len(lines):
            cont = lines[i].rstrip()
            if re.match(r'^\s*"(?:[^"\\]|\\.)*"\s*:', cont):  # next line is a new key
                break
            if re.match(r'^\s*[}\]]+\s*,?\s*$', cont):  # next line is a closing bracket
                break
            uq_cont = len(re.findall(r'(?<!\\)"', cont))  # count unescaped quotes
            if uq_cont >= 1:
                parts.append(cont)
                i += 1
                break
            parts.append(cont)
            i += 1

        result.append(prefix + '\\n'.join(parts))

    return '\n'.join(result)


def _quote_leading_zero_numbers(t: str) -> str:
    """Quote numbers with leading zeros as strings (string-aware, skips content inside strings).

    Examples:
        {"zip": 0789}          → {"zip": "0789"}
        {"price": "₹78,000"}   → {"price": "₹78,000"}  (inside string, untouched)
    """
    # match leading-zero numbers like 0789, but not 0.5 or inside words/strings
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
    """Normalize non-standard number formats to valid JSON numbers or strings.

    Examples:
        .75            → 0.75       (leading decimal)
        2.             → 2.0        (trailing decimal)
        1_000          → 1000       (underscore separators)
        0x1F           → 31         (hex literals)
        0789           → "0789"     (leading-zero → quoted string)
        Infinity / NaN → null
    """
    t = re.sub(r'(?<![.\d])\.([\d])', r'0.\1', t)  # .75 → 0.75 (add leading zero)
    t = re.sub(r'(\d)\.(?=[,\s\]\}\)]|$)', r'\g<1>.0', t)  # 2. → 2.0 (add trailing zero)
    while True:
        new_t = re.sub(r'(\d)_(\d)', r'\1\2', t)  # 1_000 → 1000 (strip underscore separators)
        if new_t == t:
            break
        t = new_t
    t = re.sub(r'\b0x([0-9a-fA-F]+)\b', lambda m: str(int(m.group(1), 16)), t)  # 0x1F → 31
    t = re.sub(r'(?<=:\s)0+(\d+)(?=[,\s}\]\n])', r'\1', t)  # 0042 → 42 (strip leading zeros after colon)
    t = _quote_leading_zero_numbers(t)
    t = re.sub(r'-?Infinity\b', 'null', t)  # Infinity / -Infinity → null
    t = re.sub(r'\bNaN\b', 'null', t)  # NaN → null
    return t


def _strip_ellipsis(t: str) -> str:
    """Remove ellipsis (...) outside of strings.

    Examples:
        [1, 2, 3, ...]   → [1, 2, 3, ]
        {"a": "text..."}  → {"a": "text..."}  (inside string, untouched)
    """
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
    """Add missing closing quote to a string that was truncated at end of input.

    Handles trailing backslashes correctly (odd count = dangling escape, strip it).

    Examples:
        {"key": "value     → {"key": "value"}
        {"key": "val\\\\    → {"key": "val\\\\"}   (even backslashes, just close)
        {"key": "val\\     → {"key": "val"}      (odd backslash stripped, then close)
    """
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
    """Remove bare backslash-escape sequences (\\x) that appear outside of strings.

    Examples:
        [1, 2\\n]        → [1, 2]      (bare \\n outside string removed)
        {"a": "b\\nc"}   → {"a": "b\\nc"}  (inside string, untouched)
    """
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


def _is_structural_quote(t: str, pos: int) -> bool:
    """Determine if a quote at position `pos` is a structural delimiter (not inner content).

    Used by _fix_unquoted_inner_quotes to decide whether a " inside a string is the
    real closing quote or an unescaped inner quote that should be escaped.

    Returns True if what follows the quote looks like valid JSON structure:
    comma, closing bracket, colon, newline, another key-value pair, etc.

    Examples (returns True):
        "value",     → comma follows
        "value"}     → closing brace follows
        "value"\\n   → newline follows (next key on new line)
        "val"key":   → bare key pattern follows (missing opening quote on key)
    """
    raw_after = t[pos + 1:]
    after = raw_after.lstrip()
    if not after:
        return True
    if after[0] in ',}]:{[':
        return True
    if raw_after and raw_after[0] == '\n':
        return True
    if after.startswith('+ "'):  # string concatenation: "a" + "b"
        return True
    if re.match(r'[a-zA-Z_]\w*"\s*:', after):  # bare key missing opening quote: c":"d"
        return True
    if after[0] == '"':  # followed by another quoted string like "key": or "value",
        rem = after[1:]
        close = rem.find('"')
        if close >= 0:
            trailing = rem[close + 1:].lstrip()[:1]
            if trailing in (':', ',', '}', ']', '') or not trailing:
                return True
    if re.match(r',\s*"[^"]*"\s*:', after):  # , "key": pattern (next key-value pair)
        return True
    if re.match(r',\s*"', after):  # , " pattern — check if it starts a key-value pair
        rest = after[1:].lstrip()
        if rest.startswith('"'):
            rem = rest[1:]
            close = rem.find('"')
            if close >= 0 and rem[close + 1:].lstrip().startswith(':'):
                return True
    return False


def _fix_missing_open_quotes(t: str) -> str:
    """Fix bare words before a closing quote where the opening quote is missing.

    String-aware — only processes bare words outside of quoted strings.
    Handles three patterns:
      1. Key position: bare word after closing quote, followed by ":
      2. Value position: bare word after : or , with a closing quote nearby
      3. Bare prefix: bare word glued to a quoted string (merged into it)

    Examples:
        [a","b"]              → ["a","b"]           (missing opening quote)
        {a":"foo","b":"bar"}  → {"a":"foo","b":"bar"}
        {"a":foo","b":"bar"}  → {"a":"foo","b":"bar"}
        {"a":"b,"c":"d"}      → {"a":"b","c":"d"}   (comma split + key fix)
        {"this": h"that"}     → {"this": "hthat"}   (bare prefix merged)
    """
    result = []
    i = 0
    in_str = False
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
            i += 1
            continue
        if ch == '"':
            in_str = True
            result.append(ch)
            i += 1
            continue
        m = re.match(r'([a-zA-Z_]\w*)"', t[i:])
        if m:
            word = m.group(1)
            prev = ''.join(result).rstrip()
            after_quote = t[i + len(word) + 1:]
            after_q = after_quote.lstrip()
            is_value_pos = prev and (prev[-1] in ':,[{' or (prev[-1] == ' ' and len(prev) >= 2 and prev[-2] in ':,'))
            is_key_pos = prev and prev[-1] == '"' and re.match(r'([a-zA-Z_]\w*)"(\s*:)', t[i:])
            if is_key_pos:
                km = re.match(r'([a-zA-Z_]\w*)"(\s*:)', t[i:])
                joined = ''.join(result)
                if len(joined) >= 2 and joined[-2] == ',':
                    result_str = joined[:-2] + '"'
                    result.clear()
                    result.append(result_str)
                result.append(',')
                result.append('"' + km.group(1) + '"' + km.group(2))
                i += len(km.group(0))
                continue
            if is_value_pos:
                if after_q and after_q[0] in ',}]:':
                    result.append('"' + word + '"')
                    i += len(word) + 1
                    continue
                closing = after_quote.find('"')
                if closing >= 0:
                    inner = after_quote[:closing]
                    after_close = after_quote[closing + 1:].lstrip()
                    if after_close and after_close[0] in ',}]':
                        result.append('"' + word + inner + '"')
                        i += len(word) + 1 + closing + 1
                        continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _fix_unquoted_inner_quotes(t: str) -> str:
    """Escape double quotes that appear inside string values (unquoted inner quotes).

    Only processes strings that open after a structural character (: { [ ,).
    Uses _is_structural_quote to decide whether each inner " is a real closing
    quote or content that should be escaped.

    Examples:
        ["some "quoted" text"]                    → ["some \\"quoted\\" text"]
        {"test": "some "quoted" text"}            → {"test": "some \\"quoted\\" text"}
        {"claim": ""this is a test" of space"}    → {"claim": "\\"this is a test\\" of space"}
    """
    result = []
    i = 0
    while i < len(t):
        ch = t[i]
        if ch != '"':
            result.append(ch)
            i += 1
            continue
        prev = ''.join(result).rstrip()
        if prev and prev[-1] not in ':{[,':
            result.append(ch)
            i += 1
            continue
        i += 1
        parts = []
        while i < len(t):
            c = t[i]
            if c == '\\' and i + 1 < len(t):
                parts.append(c)
                parts.append(t[i + 1])
                i += 2
                continue
            if c == '"':
                if _is_structural_quote(t, i):
                    break
                parts.append('\\"')
                i += 1
                continue
            parts.append(c)
            i += 1
        result.append('"')
        result.append(''.join(parts))
        if i < len(t):
            result.append('"')
            i += 1
        else:
            result.append('"')
    return ''.join(result)


def _quote_bare_words(t: str) -> str:
    """Quote unquoted bare words in value positions as strings.

    String-aware — skips content inside quoted strings. Only quotes words that
    appear after a structural character (: , [). Preserves JSON literals
    (true, false, null). Supports hyphenated words like "cool-cat".

    Examples:
        [a, b]               → ["a", "b"]
        {"this": that}       → {"this": "that"}
        {"test": cool-cat}   → {"test": "cool-cat"}
        [true, false]        → [true, false]  (literals preserved)
    """
    result = []
    i = 0
    in_str = False
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
        m = re.match(r'[a-zA-Z_][a-zA-Z0-9_\-]*', t[i:])  # match bare word (letters, digits, hyphens)
        if m:
            word = m.group(0)
            if word in ('true', 'false', 'null'):
                result.append(word)
            else:
                prev = ''.join(result).rstrip()
                if prev and prev[-1] in ':,[':
                    result.append('"' + word + '"')
                else:
                    result.append(word)
            i += len(word)
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _auto_close_brackets(t: str) -> str:
    """Close unclosed brackets/braces and fix mismatched bracket types.

    Tracks a stack of expected closing characters. Mismatched closers are
    corrected (} inside [] becomes ], and vice versa). Unclosed openers
    get their matching closer appended at the end.

    Examples:
        {"a": [1, 2}     → {"a": [1, 2]}    (} inside [] → ])
        {"a": 1           → {"a": 1}         (missing closing brace)
        [1, 2, 3          → [1, 2, 3]        (missing closing bracket)
    """
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
    """Fast regex/string-based repair for common JSON errors.

    Applies a sequence of targeted fixes in a specific order:
      1. Strip markdown fences, JSONP wrappers, preamble text
      2. Wrap bare words as strings (abc → "abc")
      3. Fix unquoted inner quotes and missing opening quotes
      4. Convert single quotes to double quotes (handles escaped \\' too)
      5. Strip comments (// and /* */)
      6. Remove ellipsis, join string concatenation ("a" + "b" → "ab")
      7. Quote unquoted keys and numeric keys ({key: 1} → {"key": 1})
      8. Insert missing colons between keys and values
      9. Quote bare words in value positions ({a: that} → {"a": "that"})
     10. Insert null for empty values ({"key": } → {"key": null})
     11. Insert missing commas between elements
     12. Insert missing } between adjacent objects ([{"i":1{"i":2}] fix)
     13. Fix number formats (.75, 0x1F, 1_000, Infinity, NaN, etc.)
     14. Escape control chars, fix backslashes, strip bare escapes
     15. Close truncated strings, auto-close brackets, fix mismatched types
     16. Remove trailing/leading commas

    Returns the repaired string. Does NOT guarantee valid JSON for all inputs —
    the LLM tiers handle cases too ambiguous for heuristics.
    """
    t = text.strip()

    t = re.sub(r'^```(?:json)?\s*\n?', '', t)  # strip opening markdown fence: ```json
    t = re.sub(r'\n?```\s*$', '', t)  # strip closing markdown fence: ```

    # unwrap JSONP callback: functionName({...}); → {...}
    m = re.match(r'^[a-zA-Z_]\w*\s*\((.*)\)\s*;?\s*$', t, re.DOTALL)
    if m and '{' not in t and '[' not in t:
        t = m.group(1).strip()

    if t and '{' not in t and '[' not in t:
        # standalone bare word with no JSON structure → wrap as quoted string
        if '"' not in t and re.match(r'^[a-zA-Z_][\w\s]*$', t):
            return '"' + t + '"'
        if t.endswith('"') and not t.startswith('"') and t.count('"') == 1:
            return '"' + t

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

    t = re.sub(r',\s*,+', ',', t)  # collapse double/triple commas: ,, → ,

    t = re.sub(r'\bTrue\b', 'true', t)  # Python True → JSON true
    t = re.sub(r'\bFalse\b', 'false', t)  # Python False → JSON false
    t = re.sub(r'\bNone\b', 'null', t)  # Python None → JSON null
    t = re.sub(r'\bundefined\b', 'null', t)  # JS undefined → JSON null

    # MongoDB extended JSON: ObjectId("abc") → "abc", ISODate("...") → "..."
    t = re.sub(r'\b[A-Z][a-zA-Z]*\("([^"]*)"\)', r'"\1"', t)

    t = _fix_multiline_strings(t)
    t = _fix_closing_quotes(t)

    t = _fix_unquoted_inner_quotes(t)
    t = _fix_missing_open_quotes(t)

    if "'" in t:
        t = _replace_single_quotes(t)

    t = _strip_comments(t)

    if '...' in t:
        t = _strip_ellipsis(t)
        t = re.sub(r',\s*,+', ',', t)

    # join string concatenation: "a" + "b" → "ab" (repeat until no more joins)
    while True:
        new_t = re.sub(r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*\+\s*"([^"\\]*(?:\\.[^"\\]*)*)"', r'"\1\2"', t)
        if new_t == t:
            break
        t = new_t

    # quote unquoted object keys: {name: → {"name":
    t = re.sub(r'(?<=[{,\n])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r' "\1":', t)
    # quote numeric object keys: {2: → {"2":
    t = re.sub(r'(?<=[{,\n])\s*(\d+)\s*:', r' "\1":', t)

    # insert missing colon between key and value: "key" "val" → "key": "val"
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(")', r'\1: \2', t, flags=re.MULTILINE)
    # "key" 123 → "key": 123
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(\d)', r'\1: \2', t, flags=re.MULTILINE)
    # "key" true → "key": true
    t = re.sub(r'^(\s*"(?:[^"\\]|\\.)*")\s+(true|false|null)', r'\1: \2', t, flags=re.MULTILINE)
    # same missing-colon fixes after { or , instead of line start
    t = re.sub(r'(?<=[{,])\s*("(?:[^"\\]|\\.)*")\s+(")', r' \1: \2', t)
    t = re.sub(r'(?<=[{,])\s*("(?:[^"\\]|\\.)*")\s+(\d)', r' \1: \2', t)
    t = re.sub(r'(?<=[{,])\s*("(?:[^"\\]|\\.)*")\s+(true|false|null)', r' \1: \2', t)
    # "key" { → "key": { (missing colon before nested object)
    t = re.sub(r'(")\s+(\{)', r'\1: \2', t)
    # "key" [ → "key": [ (missing colon before nested array)
    t = re.sub(r'(")\s+(\[)', r'\1: \2', t)

    t = _quote_bare_words(t)

    # insert null for empty values: {"key": } → {"key": null}
    t = re.sub(r'(:\s*)([\}\]])', r'\1null\2', t)
    # {"key": , → {"key": null,
    t = re.sub(r'(:\s*)(,)', r'\1null\2', t)

    # insert missing commas between newline-separated values
    t = re.sub(r'(")\s*\n(\s*")', r'\1,\n\2', t)  # "val"\n"key" → "val",\n"key"
    t = re.sub(r'(\d)\s*\n(\s*")', r'\1,\n\2', t)  # 123\n"key" → 123,\n"key"
    t = re.sub(r'(true|false|null)\s*\n(\s*")', r'\1,\n\2', t)  # true\n"key" → true,\n"key"
    t = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', t)  # }\n{ → },\n{
    t = re.sub(r'(\])\s*\n(\s*\[)', r'\1,\n\2', t)  # ]\n[ → ],\n[
    t = re.sub(r'(\})\s*\n(\s*")', r'\1,\n\2', t)  # }\n"key" → },\n"key"
    t = re.sub(r'(\])\s*\n(\s*")', r'\1,\n\2', t)  # ]\n"key" → ],\n"key"

    # insert missing } before { when digit precedes new object (truncated object)
    t = re.sub(r'(\d),?(\s*\{)', r'\1},\2', t)
    # insert missing commas between adjacent structures: }{ → },{  ][ → ],[
    t = re.sub(r'(\})(\s*\{)', r'\1,\2', t)
    t = re.sub(r'(\])(\s*\[)', r'\1,\2', t)
    t = re.sub(r'(\})(\s*\[)', r'\1,\2', t)
    t = re.sub(r'(\])(\s*\{)', r'\1,\2', t)

    # insert missing commas between space-separated values (no newline)
    t = re.sub(r'(")\s+(")', r'\1, \2', t)  # "a" "b" → "a", "b"
    t = re.sub(r'(\])\s+(")', r'\1, \2', t)  # ] "key" → ], "key"
    t = re.sub(r'(\])\s+(\[)', r'\1, \2', t)  # ] [ → ], [
    t = re.sub(r'(\})\s+(")', r'\1, \2', t)  # } "key" → }, "key"
    t = re.sub(r'(\})\s+(\{)', r'\1, \2', t)  # } { → }, {
    t = re.sub(r'(\d)\s+(")', r'\1, \2', t)  # 123 "key" → 123, "key"
    t = re.sub(r'(true|false|null)\s+(")', r'\1, \2', t)  # true "key" → true, "key"

    # fix truncated exponents: 2e+ → 2e+0, 2E- → 2E-0
    t = re.sub(r'(\d[eE][+\-]?)(?=[,\s\]\}]|$)', r'\g<1>0', t)
    # fix bare minus sign: - → -0
    t = re.sub(r'-(?=[,\s\]\}]|$)', '-0', t)

    t = _fix_numbers(t)
    t = _escape_control_chars(t)
    # escape invalid backslashes: \q → \\q (only valid JSON escapes are "\/bfnrtu)
    t = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', t)

    t = _strip_bare_escapes(t)
    t = _close_truncated_strings(t)
    t = _auto_close_brackets(t)

    # second pass: insert null for empty values created by auto-close
    t = re.sub(r'(:\s*)([\}\]])', r'\1null\2', t)
    # lone key in object with no value: {"key"} → {"key":null}
    t = re.sub(r'^(\s*\{\s*)("(?:[^"\\]|\\.)*")(\s*\}\s*)$', r'\1\2:null\3', t)

    # remove trailing commas before closing brackets: [1,] → [1]
    for _ in range(3):
        t = re.sub(r',(\s*[}\]])', r'\1', t)

    # remove leading commas after opening brackets: [, 1] → [1]
    t = re.sub(r'([\[{])(\s*),(\s*)', r'\1\2\3', t)

    return t


def resolve_refs(schema: dict) -> dict:
    """Inline $defs/$ref references in a JSON Schema so it's self-contained.

    Resolves Pydantic-style schemas where nested models are in $defs and
    referenced via $ref pointers. Mutates and returns the input schema.

    Example:
        {"$defs": {"Addr": {"type": "object"}}, "properties": {"addr": {"$ref": "#/$defs/Addr"}}}
        → {"properties": {"addr": {"type": "object"}}}
    """
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
    """Check if a JSON string parses and validates against an optional JSON Schema.

    Returns True if valid JSON (and schema validates if provided). Returns True
    if jsonschema is not installed (graceful degradation). Returns False on
    parse errors or validation failures.
    """
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
    """Fix type mismatches by coercing values to match the schema's expected types.

    Parses the JSON, runs schema validation, then surgically fixes each type error:
    int→str, str→int, str→bool, non-array→[wrapped], etc. Returns the fixed JSON
    string if all errors were resolved, or None if coercion wasn't possible.

    Examples:
        {"age": "30"} with schema expecting integer  → {"age": 30}
        {"active": "true"} with schema expecting bool → {"active": true}
        {"tags": "one"} with schema expecting array   → {"tags": ["one"]}
    """
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

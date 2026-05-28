#!/usr/bin/env python3
"""Benchmark runner for json-repair service."""

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"
SCHEMAS_DIR = TESTS_DIR / "schemas"


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


def load_pydantic_schema(schema_path: Path, class_name: str) -> dict:
    spec = importlib.util.spec_from_file_location("_schema", schema_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, class_name)
    return resolve_refs(cls.model_json_schema())


def find_root_model(schema_path: Path) -> str:
    from pydantic import BaseModel
    spec = importlib.util.spec_from_file_location("_schema", schema_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    models = []
    for name, val in vars(mod).items():
        if isinstance(val, type) and issubclass(val, BaseModel) and val is not BaseModel:
            models.append((name, val))
    # Last defined model is typically the root
    return models[-1][0] if models else ""


def repair_via_api(base_url: str, broken_json: str, schema: dict, api_key: str | None = None) -> tuple[dict, float]:
    body = {"broken_json": broken_json, "schema_dict": schema}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    data = json.dumps(body).encode()
    req = Request(f"{base_url}/repair", data=data, headers=headers)

    start = time.time()
    resp = urlopen(req, timeout=120)
    elapsed = time.time() - start

    return json.loads(resp.read()), elapsed


def validate_structure(repaired: str, schema_path: Path, class_name: str) -> tuple[bool, str]:
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    try:
        spec = importlib.util.spec_from_file_location("_schema", schema_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, class_name)
        cls.model_validate(parsed)
        return True, "Pydantic validation passed"
    except Exception as e:
        return False, f"Pydantic validation failed: {e}"


def run_external_benchmark(base_url: str | None = None, api_key: str | None = None):
    """Run external test cases — deterministic-only locally, or full pipeline via server."""
    cases_path = TESTS_DIR / "external" / "extracted_cases.json"
    if not cases_path.exists():
        print(f"External cases not found: {cases_path}")
        return

    with open(cases_path) as f:
        cases = json.load(f)

    use_server = base_url is not None
    if not use_server:
        sys.path.insert(0, str(TESTS_DIR.parent))
        from scripts.repair import deterministic_repair

    mode = f"server ({base_url})" if use_server else "deterministic only"
    results = []
    total_time = 0
    print(f"Running {len(cases)} external cases ({mode})\n")
    if use_server:
        print(f"{'#':<4} {'Input':<50} {'Result':<7} {'Method':<14} {'Time':>6}  {'Match'}")
    else:
        print(f"{'#':<4} {'Input':<50} {'Result':<7} {'Match'}")
    print("-" * (95 if use_server else 80))

    for i, case in enumerate(cases, 1):
        inp = case["input"]
        expected = case.get("expected", "")
        source = case.get("source", "")
        display = inp[:48].replace('\n', '\\n')

        if use_server:
            try:
                result, elapsed = repair_via_api(base_url, inp, {}, api_key)
                total_time += elapsed
                repaired = result.get("repaired_json", "")
                method = result.get("method", "llm")
                valid = result.get("valid", False)
                if not valid:
                    try:
                        json.loads(repaired)
                        valid = True
                    except json.JSONDecodeError:
                        pass
            except Exception as e:
                repaired = ""
                method = "error"
                valid = False
                elapsed = 0
        else:
            repaired = deterministic_repair(inp)
            method = "deterministic"
            elapsed = 0
            try:
                json.loads(repaired)
                valid = True
            except json.JSONDecodeError:
                valid = False

        matches = repaired.strip() == expected.strip() if expected else False

        status = "OK" if valid else "FAIL"
        match_str = "exact" if matches else ("valid" if valid else "—")

        if use_server:
            print(f"{i:<4} {display:<50} {status:<7} {method:<14} {elapsed:>5.1f}s  {match_str}")
        else:
            print(f"{i:<4} {display:<50} {status:<7} {match_str}")

        results.append({
            "input": inp,
            "expected": expected,
            "repaired": repaired,
            "valid_json": valid,
            "exact_match": matches,
            "source": source,
            **({"method": method, "time_s": round(elapsed, 2)} if use_server else {}),
        })

    print("-" * (95 if use_server else 80))

    valid_count = sum(1 for r in results if r["valid_json"])
    exact_count = sum(1 for r in results if r["exact_match"])
    print(f"\nResults: {valid_count}/{len(results)} valid JSON, {exact_count}/{len(results)} exact match")
    if use_server:
        det_count = sum(1 for r in results if r.get("method", "").startswith("deterministic"))
        llm_count = sum(1 for r in results if r.get("method") in ("llm",) or (r.get("method", "").startswith("snippet")))
        avg_time = total_time / len(results) if results else 0
        print(f"Methods: {det_count} deterministic, {llm_count} LLM/snippet")
        print(f"Total time: {total_time:.1f}s, Avg: {avg_time:.1f}s per case")

    report_path = TESTS_DIR / "benchmark_results_external.json"
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "set": "external",
            "mode": "server" if use_server else "deterministic",
            **({"base_url": base_url} if use_server else {}),
            "total_cases": len(results),
            "valid_json": valid_count,
            "exact_match": exact_count,
            "results": results,
        }, f, indent=2)
    print(f"Report saved to {report_path}")


def run_benchmark(base_url: str, api_key: str | None = None, case_filter: str | None = None, validation: bool = False):
    base_dir = TESTS_DIR / "validation" if validation else TESTS_DIR
    cases_dir = base_dir / "cases"
    schemas_dir = base_dir / "schemas" if validation else SCHEMAS_DIR

    cases = sorted(cases_dir.glob("*.json"))
    if case_filter:
        cases = [c for c in cases if case_filter in c.name]

    results = []
    total_time = 0

    label = "validation" if validation else "test"
    print(f"Running {len(cases)} {label} cases against {base_url}\n")
    print(f"{'Case':<35} {'Valid':<7} {'Pydantic':<10} {'Method':<14} {'Time':>6}  Status")
    print("-" * 95)

    for case_path in cases:
        num = case_path.stem.split("_")[0]
        schema_candidates = list(schemas_dir.glob(f"s{num}_*.py"))
        if not schema_candidates:
            print(f"{case_path.stem:<35} {'—':<7} {'—':<10} {'—':>6}  SKIP: no schema found")
            continue

        schema_path = schema_candidates[0]
        class_name = find_root_model(schema_path)
        if not class_name:
            print(f"{case_path.stem:<35} {'—':<7} {'—':<10} {'—':>6}  SKIP: no Pydantic model found")
            continue

        broken = case_path.read_text()
        schema = load_pydantic_schema(schema_path, class_name)

        try:
            result, elapsed = repair_via_api(base_url, broken, schema, api_key)
            total_time += elapsed

            valid_json = result.get("valid", False)
            repaired = result.get("repaired_json", "")
            method = result.get("method", "llm")

            pydantic_ok, pydantic_msg = validate_structure(repaired, schema_path, class_name)

            status = "PASS" if valid_json and pydantic_ok else "FAIL"
            if not valid_json:
                status_detail = "invalid JSON"
            elif not pydantic_ok:
                status_detail = pydantic_msg[:40]
            else:
                status_detail = ""

            print(f"{case_path.stem:<35} {'OK' if valid_json else 'FAIL':<7} {'OK' if pydantic_ok else 'FAIL':<10} {method:<14} {elapsed:>5.1f}s  {status} {status_detail}")

            results.append({
                "case": case_path.stem,
                "schema": f"{schema_path.stem}:{class_name}",
                "valid_json": valid_json,
                "pydantic_valid": pydantic_ok,
                "pydantic_msg": pydantic_msg,
                "method": method,
                "time_s": round(elapsed, 2),
                "pass": valid_json and pydantic_ok,
            })

        except Exception as e:
            print(f"{case_path.stem:<35} {'—':<7} {'—':<10} {'—':>6}  ERROR: {e}")
            results.append({
                "case": case_path.stem,
                "schema": f"{schema_path.stem}:{class_name}",
                "valid_json": False,
                "pydantic_valid": False,
                "pydantic_msg": str(e),
                "time_s": 0,
                "pass": False,
            })

    print("-" * 95)

    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed
    det_count = sum(1 for r in results if r.get("method") == "deterministic")
    llm_count = sum(1 for r in results if r.get("method") == "llm")
    avg_time = total_time / len(results) if results else 0

    print(f"\nResults: {passed}/{len(results)} passed, {failed} failed")
    print(f"Methods: {det_count} deterministic, {llm_count} LLM")
    print(f"Total time: {total_time:.1f}s, Avg: {avg_time:.1f}s per case")

    suffix = "_validation" if validation else ""
    report_path = TESTS_DIR / f"benchmark_results{suffix}.json"
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "base_url": base_url,
            "set": label,
            "total_cases": len(results),
            "passed": passed,
            "failed": failed,
            "deterministic": det_count,
            "llm": llm_count,
            "total_time_s": round(total_time, 2),
            "avg_time_s": round(avg_time, 2),
            "results": results,
        }, f, indent=2)
    print(f"Report saved to {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark json-repair service")
    parser.add_argument("--url", default=os.environ.get("REPAIR_URL", "http://localhost:8080"), help="Service base URL (or set REPAIR_URL env var)")
    parser.add_argument("--api-key", default=os.environ.get("REPAIR_API_KEY"), help="API key for X-API-Key header (or set REPAIR_API_KEY env var)")
    parser.add_argument("--filter", help="Only run cases matching this string")
    parser.add_argument("--validation", action="store_true", help="Run validation set instead of test set")
    parser.add_argument("--external", action="store_true", help="Run external test cases (deterministic only, no server needed)")
    args = parser.parse_args()

    if args.external:
        use_server = args.api_key or os.environ.get("REPAIR_URL") or "--url" in sys.argv
        run_external_benchmark(args.url if use_server else None, args.api_key)
    else:
        run_benchmark(args.url, args.api_key, args.filter, args.validation)


if __name__ == "__main__":
    main()

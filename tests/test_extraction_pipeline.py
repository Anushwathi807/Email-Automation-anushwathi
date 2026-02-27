#!/usr/bin/env python3
"""
JobCart Email Automation — Extraction Pipeline Test Runner

Runs mock email thread scenarios through the full extraction pipeline
(agent_runner.run_agent_step_async) and saves structured results as JSON.

Usage:
    python -m tests.test_extraction_pipeline

Requirements:
    - Valid GROQ_API_KEY in .env (uses real LLM, no Gmail needed)

Output:
    - tests/results/extraction_test_results.json
    - Console summary with pass/fail per scenario
"""

import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.mock_email_scenarios import ALL_SCENARIOS


# ─────────────────────────────────────────────
# Accuracy Checker
# ─────────────────────────────────────────────
def _get_all_finalized_employees(result: Dict[str, Any]) -> List[str]:
    """Extract all finalized employee names from the result, searching all requirement levels."""
    names: List[str] = []

    # Top-level finalized_employees
    top_level = result.get("finalized_employees", [])
    if isinstance(top_level, list):
        names.extend(top_level)

    # From parsed_output.requirements
    parsed = result.get("parsed_output", {})
    if isinstance(parsed, dict):
        for req in parsed.get("requirements", []):
            if isinstance(req, dict):
                req_names = req.get("finalized_employees", [])
                if isinstance(req_names, list):
                    names.extend(req_names)

    # From raw Requirements (if present at top level of raw_output)
    raw_str = result.get("raw_output", "")
    if isinstance(raw_str, str) and raw_str.strip():
        try:
            raw = json.loads(raw_str)
            if isinstance(raw, dict):
                for req in raw.get("Requirements", []):
                    if isinstance(req, dict):
                        req_names = req.get("finalized_employees", [])
                        if isinstance(req_names, list):
                            names.extend(req_names)
        except (json.JSONDecodeError, TypeError):
            pass

    return names


def _get_all_requirements(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract all requirement dicts from the result."""
    reqs: List[Dict[str, Any]] = []

    parsed = result.get("parsed_output", {})
    if isinstance(parsed, dict):
        for req in parsed.get("requirements", []):
            if isinstance(req, dict):
                reqs.append(req)

    return reqs


def check_accuracy(result: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run accuracy checks on the extraction result against expected values.
    Returns a dict of {check_name: {"passed": bool, "detail": str}}.
    """
    checks = expected.get("checks", {})
    report: Dict[str, Any] = {}

    all_employees = _get_all_finalized_employees(result)
    all_employees_lower = [n.lower() for n in all_employees]
    all_requirements = _get_all_requirements(result)

    # Check: valid_thread
    if "valid_thread" in checks:
        actual = bool(result.get("valid_thread", False))
        expected_val = checks["valid_thread"]
        report["valid_thread"] = {
            "passed": actual == expected_val,
            "expected": expected_val,
            "actual": actual,
        }

    # Check: has_requirements
    if "has_requirements" in checks:
        has_reqs = len(all_requirements) > 0
        report["has_requirements"] = {
            "passed": has_reqs == checks["has_requirements"],
            "expected": checks["has_requirements"],
            "actual": has_reqs,
            "count": len(all_requirements),
        }

    # Check: min_requirements_count
    if "min_requirements_count" in checks:
        count = len(all_requirements)
        min_expected = checks["min_requirements_count"]
        report["min_requirements_count"] = {
            "passed": count >= min_expected,
            "expected_min": min_expected,
            "actual": count,
        }

    # Check: min_finalized_employees
    if "min_finalized_employees" in checks:
        count = len(all_employees)
        min_expected = checks["min_finalized_employees"]
        report["min_finalized_employees"] = {
            "passed": count >= min_expected,
            "expected_min": min_expected,
            "actual": count,
            "names": all_employees,
        }

    # Check: expected_shift_date
    if "expected_shift_date" in checks:
        expected_date = checks["expected_shift_date"]
        found = False
        actual_dates = []
        for req in all_requirements:
            sd = req.get("shift_date", "")
            actual_dates.append(sd)
            if expected_date in str(sd):
                found = True
        report["expected_shift_date"] = {
            "passed": found,
            "expected": expected_date,
            "actual_dates": actual_dates,
        }

    # Check: expected_shift_time
    if "expected_shift_time" in checks:
        expected_time = checks["expected_shift_time"].lower()
        found = False
        actual_times = []
        for req in all_requirements:
            st = str(req.get("shift_time", "")).strip()
            actual_times.append(st)
            if expected_time in st.lower():
                found = True
        report["expected_shift_time"] = {
            "passed": found,
            "expected": checks["expected_shift_time"],
            "actual_times": actual_times,
        }

    # Check: expected_location_contains
    if "expected_location_contains" in checks:
        expected_loc = checks["expected_location_contains"].lower()
        found = False
        actual_locs = []
        for req in all_requirements:
            loc = str(req.get("location_name", "")).strip()
            actual_locs.append(loc)
            if expected_loc in loc.lower():
                found = True
        report["expected_location_contains"] = {
            "passed": found,
            "expected_contains": checks["expected_location_contains"],
            "actual_locations": actual_locs,
        }

    # Check: expected_status
    if "expected_status" in checks:
        expected_status = checks["expected_status"].lower()
        found = False
        actual_statuses = []
        for req in all_requirements:
            s = str(req.get("status", "")).strip().lower()
            actual_statuses.append(s)
            if expected_status == s:
                found = True
        report["expected_status"] = {
            "passed": found,
            "expected": checks["expected_status"],
            "actual_statuses": actual_statuses,
        }

    # Check: expected_finalized_must_include
    if "expected_finalized_must_include" in checks:
        must_include = checks["expected_finalized_must_include"]
        missing = []
        for name in must_include:
            name_lower = name.lower()
            # Fuzzy check: any employee name containing the expected name tokens
            name_tokens = set(name_lower.split())
            found = False
            for emp in all_employees_lower:
                emp_tokens = set(emp.split())
                if name_tokens.issubset(emp_tokens):
                    found = True
                    break
            if not found:
                missing.append(name)
        report["expected_finalized_must_include"] = {
            "passed": len(missing) == 0,
            "expected": must_include,
            "missing": missing,
            "actual_employees": all_employees,
        }

    # Check: expected_finalized_must_exclude
    if "expected_finalized_must_exclude" in checks:
        must_exclude = checks["expected_finalized_must_exclude"]
        wrongly_included = []
        for name in must_exclude:
            name_lower = name.lower()
            name_tokens = set(name_lower.split())
            for emp in all_employees_lower:
                emp_tokens = set(emp.split())
                if name_tokens.issubset(emp_tokens):
                    wrongly_included.append(name)
                    break
        report["expected_finalized_must_exclude"] = {
            "passed": len(wrongly_included) == 0,
            "expected_excluded": must_exclude,
            "wrongly_included": wrongly_included,
            "actual_employees": all_employees,
        }

    return report


# ─────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────
async def run_single_scenario(
    name: str, thread_data: Dict[str, Any], expected: Dict[str, Any]
) -> Dict[str, Any]:
    """Run a single scenario through the extraction pipeline."""
    from agent.agent_runner import run_agent_step_async

    record: Dict[str, Any] = {
        "scenario": name,
        "description": expected.get("description", ""),
        "thread_id": thread_data.get("threadId", ""),
        "message_count": len(thread_data.get("messages", [])),
    }

    start_time = time.time()
    try:
        result = await run_agent_step_async(thread_data)
        elapsed = round(time.time() - start_time, 3)

        record["status"] = "success"
        record["elapsed_seconds"] = elapsed
        record["extraction_result"] = result

        # Run accuracy checks
        accuracy = check_accuracy(result, expected)
        all_passed = all(c.get("passed", False) for c in accuracy.values())
        record["accuracy_checks"] = accuracy
        record["all_checks_passed"] = all_passed

    except Exception as e:
        elapsed = round(time.time() - start_time, 3)
        record["status"] = "error"
        record["elapsed_seconds"] = elapsed
        record["error"] = str(e)
        record["traceback"] = traceback.format_exc()
        record["accuracy_checks"] = {}
        record["all_checks_passed"] = False

    return record


async def run_all_scenarios() -> Dict[str, Any]:
    """Run all mock email scenarios and compile results."""
    print("=" * 60)
    print("  🧪 JobCart Extraction Pipeline — Test Runner")
    print("=" * 60)
    print()

    results: List[Dict[str, Any]] = []
    total_start = time.time()

    for idx, (name, thread_data, expected) in enumerate(ALL_SCENARIOS, start=1):
        print(f"  [{idx}/{len(ALL_SCENARIOS)}] Running: {name}")
        print(f"         {expected.get('description', '')}")

        record = await run_single_scenario(name, thread_data, expected)
        results.append(record)

        status_icon = "✅" if record["all_checks_passed"] else ("❌" if record["status"] == "error" else "⚠️")
        print(f"         {status_icon} {record['status']} in {record['elapsed_seconds']}s")

        if record.get("accuracy_checks"):
            for check_name, check_result in record["accuracy_checks"].items():
                icon = "✓" if check_result.get("passed") else "✗"
                print(f"           {icon} {check_name}")

        print()

    total_elapsed = round(time.time() - total_start, 3)

    # Summary
    passed = sum(1 for r in results if r["all_checks_passed"])
    failed = sum(1 for r in results if not r["all_checks_passed"] and r["status"] == "success")
    errors = sum(1 for r in results if r["status"] == "error")

    summary = {
        "total_scenarios": len(results),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_elapsed_seconds": total_elapsed,
        "avg_extraction_seconds": round(
            sum(r["elapsed_seconds"] for r in results) / max(len(results), 1), 3
        ),
    }

    print("=" * 60)
    print(f"  📊 RESULTS: {passed} passed, {failed} failed, {errors} errors")
    print(f"  ⏱  Total time: {total_elapsed}s | Avg per scenario: {summary['avg_extraction_seconds']}s")
    print("=" * 60)

    # Full output
    output = {
        "test_run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": "jobcart-email-automation",
            "test_type": "extraction_pipeline",
            "groq_model": "llama-3.3-70b-versatile",
        },
        "summary": summary,
        "scenarios": results,
    }

    return output


def save_results(output: Dict[str, Any], path: str) -> None:
    """Save test results to a JSON file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 Results saved to: {path}")


# ─────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────
def main():
    output = asyncio.run(run_all_scenarios())

    results_path = os.path.join(PROJECT_ROOT, "tests", "results", "extraction_test_results.json")
    save_results(output, results_path)

    # Exit code: 0 if all passed, 1 otherwise
    if output["summary"]["passed"] == output["summary"]["total_scenarios"]:
        print("\n  🎉 All scenarios passed!\n")
        return 0
    else:
        print("\n  ⚠️  Some scenarios did not pass. Review the JSON output for details.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

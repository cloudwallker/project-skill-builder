#!/usr/bin/env python3
"""Evaluate the CSV example's documented behavior using Python's standard library.

Usage: python evals/grade_csv.py PROJECT

PROJECT is the directory containing summary.py. Tests call summarize(path) with
temporary CSV files; they do not inspect or modify the candidate source code.
"""

import argparse
import contextlib
import csv
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import io
import json
from pathlib import Path
import pkgutil
import re
import subprocess
import sys
import sysconfig
import tempfile
from decimal import Decimal


TEST_IDS = (
    "public_api",
    "exact_decimal_arithmetic",
    "category_whitespace",
    "header_only",
    "malformed_amount",
    "empty_amount",
    "missing_category",
    "whitespace_category",
    "missing_cell",
    "missing_amount_header",
    "missing_category_header",
    "stdlib_runtime",
)


class _BehaviorFailure(AssertionError):
    """An evaluator assertion whose diagnostic is safe to publish."""


def _stdlib_names():
    """Use the running interpreter's library, including Python 3.9 runtimes."""
    names = set(sys.builtin_module_names)
    if hasattr(sys, "stdlib_module_names"):
        names.update(sys.stdlib_module_names)
        return names
    library_paths = {
        sysconfig.get_path("stdlib"),
        sysconfig.get_path("platstdlib"),
        str(Path(sys.base_prefix) / "DLLs"),
    }
    for directory in library_paths:
        if directory and Path(directory).is_dir():
            names.update(item.name for item in pkgutil.iter_modules([directory]))
    return names


def _within(path, directory):
    try:
        Path(path).resolve().relative_to(directory)
        return True
    except (OSError, ValueError):
        return False


class _StandardLibraryGuard(importlib.abc.MetaPathFinder):
    """Permit stdlib imports and local project helpers; reject external packages."""

    def __init__(self, project):
        self.project = project
        self.names = _stdlib_names()
        self.rejected = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in self.names:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None:
            origins = list(spec.submodule_search_locations or ())
            if spec.origin and spec.origin not in ("built-in", "frozen"):
                origins.append(spec.origin)
            if origins and all(_within(origin, self.project) for origin in origins):
                return None
        self.rejected.add(fullname.partition(".")[0])
        raise ModuleNotFoundError("Non-stdlib dependency: " + fullname)


def _row_numbered(message, number):
    """Accept ordinary English/Chinese row or line labels, without exact wording."""
    patterns = (
        r"\b(?:row|line)\s*(?:number\s*)?[#:=]?\s*" + str(number) + r"\b",
        r"第?\s*" + str(number) + r"\s*行",
        r"行\s*[#:=：]?\s*" + str(number) + r"(?!\d)",
    )
    return any(re.search(pattern, message, re.IGNORECASE) for pattern in patterns)


def _short_error(error):
    # Do not expose a candidate's paths or input values in the report.
    return type(error).__name__


def _load_summary(project):
    source = project / "summary.py"
    if not source.is_file():
        raise FileNotFoundError("PROJECT must contain summary.py")
    spec = importlib.util.spec_from_file_location("summary", source)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load summary.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["summary"] = module
    spec.loader.exec_module(module)
    return module


def _grade_worker(project):
    results = []
    guard = _StandardLibraryGuard(project)
    sys.path.insert(0, str(project))
    sys.meta_path.insert(0, guard)
    try:
        # Candidate diagnostics cannot corrupt the machine-readable report.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module = _load_summary(project)
            summarize = getattr(module, "summarize", None)
    except BaseException as error:
        detail = "summary.py could not be imported: " + _short_error(error)
        results = [{"id": test_id, "passed": False, "detail": detail} for test_id in TEST_IDS]
        return _report(results)

    with tempfile.TemporaryDirectory(prefix="csv-behavior-") as directory:
        csv_path = Path(directory) / "input.csv"

        def call(rows):
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                csv.writer(stream).writerows(rows)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return summarize(csv_path)

        def expect_totals(rows, expected):
            actual = call(rows)
            if not isinstance(actual, dict):
                raise _BehaviorFailure("summarize(path) must return a dictionary")
            if any(not isinstance(value, Decimal) for value in actual.values()):
                raise _BehaviorFailure("currency totals must be Decimal values")
            if actual != expected:
                raise _BehaviorFailure("aggregation result differs from the documented result")

        def expect_error(rows, row_number, missing_header=None):
            try:
                call(rows)
            except ValueError as error:
                message = str(error)
                # README does not specify whether the header counts as a row.
                # Accept either physical CSV lines or one-based data-row numbers.
                numbers = (row_number,) if missing_header else (row_number, row_number - 1)
                if not any(_row_numbered(message, number) for number in numbers):
                    raise _BehaviorFailure("ValueError must identify the offending CSV row")
                if missing_header and missing_header not in message.lower():
                    raise _BehaviorFailure("header error must identify missing column " + missing_header)
            except BaseException as error:
                raise _BehaviorFailure("expected ValueError, received " + _short_error(error)) from None
            else:
                raise _BehaviorFailure("invalid CSV must fail instead of returning a result")

        def public_api():
            if not callable(summarize):
                raise _BehaviorFailure("summary.summarize must remain callable")
            try:
                inspect.signature(summarize).bind(csv_path)
            except (TypeError, ValueError):
                raise _BehaviorFailure("summarize(path) must accept one positional path") from None
            expect_totals([['category', 'amount'], ['books', '2.50']], {'books': Decimal('2.50')})

        def stdlib_runtime():
            # Also exercise the function, so lazy external imports are checked.
            expect_totals([['category', 'amount'], ['books', '2.50']], {'books': Decimal('2.50')})
            if guard.rejected:
                raise _BehaviorFailure("runtime attempted a non-stdlib dependency")

        tests = (
            ("public_api", public_api),
            ("exact_decimal_arithmetic", lambda: expect_totals(
                [['category', 'amount'], ['small', '0.1'], ['small', '0.2'],
                 ['large', '9007199254740993.01'], ['large', '0.09']],
                {'small': Decimal('0.3'), 'large': Decimal('9007199254740993.10')})),
            ("category_whitespace", lambda: expect_totals(
                [['category', 'amount'], [' food ', '1.25'], ['\tfood\t', '2.75']],
                {'food': Decimal('4.00')})),
            ("header_only", lambda: expect_totals([['category', 'amount']], {})),
            ("malformed_amount", lambda: expect_error(
                [['category', 'amount'], ['valid', '1.00'], ['broken', 'not-money']], 3)),
            ("empty_amount", lambda: expect_error(
                [['category', 'amount'], ['broken', '']], 2)),
            ("missing_category", lambda: expect_error(
                [['category', 'amount'], ['valid', '1.00'], ['', '2.00']], 3)),
            ("whitespace_category", lambda: expect_error(
                [['category', 'amount'], ['   ', '2.00']], 2)),
            ("missing_cell", lambda: expect_error(
                [['category', 'amount'], ['valid', '1.00'], ['broken']], 3)),
            ("missing_amount_header", lambda: expect_error([['category']], 1, 'amount')),
            ("missing_category_header", lambda: expect_error([['amount'], ['1.00']], 1, 'category')),
            ("stdlib_runtime", stdlib_runtime),
        )
        for test_id, test in tests:
            try:
                test()
            except _BehaviorFailure as error:
                results.append({"id": test_id, "passed": False, "detail": str(error)})
            except BaseException as error:
                results.append({"id": test_id, "passed": False,
                                "detail": "unexpected exception: " + _short_error(error)})
            else:
                results.append({"id": test_id, "passed": True, "detail": "documented behavior satisfied"})
    return _report(results)


def _report(results):
    passed = sum(test['passed'] for test in results)
    return {'tests': results, 'totals': {'total': len(results), 'passed': passed, 'failed': len(results) - passed}}


def _failure_report(detail):
    return _report([{'id': test_id, 'passed': False, 'detail': detail} for test_id in TEST_IDS])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', metavar='PROJECT', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    project = args.project.resolve()
    if args.worker:
        report = _grade_worker(project)
    else:
        # Isolate the candidate from the grader process and ambient PYTHONPATH.
        try:
            process = subprocess.run(
                [sys.executable, '-I', str(Path(__file__).resolve()), str(project), '--worker'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
                encoding='utf-8', errors='replace',
            )
            if process.returncode != 0:
                report = _failure_report('candidate worker failed')
            else:
                try:
                    report = json.loads(process.stdout)
                except (ValueError, TypeError):
                    report = _failure_report('candidate worker did not return a JSON report')
        except subprocess.TimeoutExpired:
            report = _failure_report('candidate evaluation exceeded 30 seconds')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

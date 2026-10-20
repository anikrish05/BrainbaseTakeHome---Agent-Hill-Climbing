"""Trusted, data-only grading for declared CSV/JSON output artifacts.

CSV columns and rows are ordered. Text is exact (including whitespace); canonical
numeric cells allow an absolute 1e-6 tolerance. Identifier-like columns and padded
numbers remain exact. JSON object ordering is ignored and array ordering matters.
No agent-generated Python, shell, configuration, or import is ever executed here.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import stat
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path
from typing import Any


CHECKER_VERSION = "structured-artifact-v1"
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
NUMERIC_TOLERANCE = Decimal("0.000001")
_NUMBER = re.compile(r"[-+]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?\Z", re.ASCII)
_IDENTIFIER = re.compile(r"(?:^|_)(?:id|identifier|code|zip|postcode|postal)(?:$|_)", re.I)


class _InvalidData(ValueError):
    pass


def _safe_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and ":" not in name
        and "\x00" not in name
        and not any(ord(char) < 32 for char in name)
        and len(name) <= 255
    )


def _csv_data(text: str) -> tuple[list[str], list[list[str]]]:
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (csv.Error, UnicodeError) as exc:
        raise _InvalidData(f"Malformed CSV: {exc}") from exc
    if not rows or not rows[0]:
        raise _InvalidData("CSV must contain a header row")
    header = rows[0]
    if any(not column for column in header) or len(set(header)) != len(header):
        raise _InvalidData("CSV headers must be nonempty and unique")
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise _InvalidData(
                f"CSV row {index} has {len(row)} cells; header has {len(header)}"
            )
    return header, rows[1:]


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidData(f"Duplicate JSON object key: {key[:80]}")
        result[key] = value
    return result


def _bad_json_constant(value: str) -> Any:
    raise _InvalidData(f"Non-finite JSON number: {value}")


def _json_data(text: str) -> Any:
    try:
        return json.loads(
            text,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=_bad_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, RecursionError, InvalidOperation) as exc:
        raise _InvalidData(f"Malformed JSON: {str(exc)[:160]}") from exc


def _parse(name: str, text: str) -> Any:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return _csv_data(text)
    if suffix == ".json":
        return _json_data(text)
    raise _InvalidData("Only CSV and JSON expected artifacts are supported")


def _numeric_equal(actual: str, expected: str) -> bool:
    if not _NUMBER.fullmatch(actual) or not _NUMBER.fullmatch(expected):
        return False
    try:
        return abs(Decimal(actual) - Decimal(expected)) <= NUMERIC_TOLERANCE
    except (DecimalException, OverflowError):
        return False


def _json_difference(actual: Any, expected: Any, path: str = "$") -> str | None:
    if isinstance(expected, Decimal):
        if not isinstance(actual, Decimal):
            return f"{path}: expected a number"
        try:
            if abs(actual - expected) > NUMERIC_TOLERANCE:
                return f"{path}: numeric value differs"
        except (DecimalException, OverflowError):
            return f"{path}: invalid numeric value"
        return None
    if type(actual) is not type(expected):
        return f"{path}: value type differs"
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            return f"{path}: object keys differ"
        for key in expected:
            difference = _json_difference(actual[key], expected[key], f"{path}.{key[:80]}")
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        if len(actual) != len(expected):
            return f"{path}: array length differs ({len(actual)} vs {len(expected)})"
        for index, (left, right) in enumerate(zip(actual, expected)):
            difference = _json_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
        return None
    return None if actual == expected else f"{path}: value differs"


def _read_regular_file(raw_path: Any) -> str:
    if not isinstance(raw_path, (str, os.PathLike)):
        raise _InvalidData("Artifact has no valid storage path")
    path = Path(raw_path)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise _InvalidData("Symbolic-link artifacts are not allowed")
        if not stat.S_ISREG(metadata.st_mode):
            raise _InvalidData("Output artifact must be a regular file")
        # O_NOFOLLOW closes the final-component symlink race on supported hosts.
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise _InvalidData("Output artifact must be a regular file")
            if metadata.st_size > MAX_ARTIFACT_BYTES:
                raise _InvalidData("Output artifact exceeds the 2 MiB limit")
            content = handle.read(MAX_ARTIFACT_BYTES + 1)
            if len(content) > MAX_ARTIFACT_BYTES:
                raise _InvalidData("Output artifact exceeds the 2 MiB limit")
    except OSError as exc:
        # Do not disclose host paths through evaluator errors.
        raise _InvalidData("Output artifact cannot be read as a regular file") from exc
    try:
        return content.decode("utf-8-sig")
    except UnicodeError as exc:
        raise _InvalidData("Output artifact is not valid UTF-8") from exc


def validate_case(case: dict) -> None:
    """Raise ValueError when an expected artifact cannot be graded safely."""
    name = case.get("expected_name")
    content = case.get("expected_content")
    if not _safe_name(name):
        raise ValueError("Expected artifact name must be a plain filename")
    if not isinstance(content, str):
        raise ValueError("Expected artifact content must be text")
    if len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ValueError("Expected artifact exceeds the 2 MiB limit")
    _parse(name, content.removeprefix("\ufeff"))


def evaluate_output(
    artifacts: list[dict], expected_name: str, expected_content: str
) -> dict:
    """Grade one expected artifact using a trusted, server-owned expectation.

    Artifacts are the collector's local manifest ({name, path, size}). The actual
    file size is verified independently of the manifest's advisory size field.
    All failures return structured results; expected data is never sent to tools.
    """
    details: dict[str, Any] = {
        "checker_version": CHECKER_VERSION,
        "expected_name": expected_name,
        "row_order": "significant",
        "numeric_tolerance": str(NUMERIC_TOLERANCE),
    }

    def fail(code: str, error: str) -> dict:
        return {"passed": False, "error": error, "details": {**details, "code": code}}

    try:
        validate_case({"expected_name": expected_name, "expected_content": expected_content})
        expected = _parse(expected_name, expected_content.removeprefix("\ufeff"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        return fail("invalid_expectation", f"Invalid expected artifact: {exc}")

    if not isinstance(artifacts, list) or any(
        not isinstance(item, dict) or not _safe_name(item.get("name")) for item in artifacts
    ):
        return fail("unsafe_artifact", "Artifact names must be plain filenames")
    matches = [item for item in artifacts if item["name"] == expected_name]
    if not matches:
        return fail("missing_artifact", f"Expected output file {expected_name} was not produced")
    if len(matches) != 1:
        return fail("duplicate_artifact", "Multiple output artifacts have the expected filename")
    try:
        actual = _parse(expected_name, _read_regular_file(matches[0].get("path")))
    except (ValueError, RecursionError) as exc:
        return fail("invalid_output", str(exc))

    if expected_name.lower().endswith(".csv"):
        actual_header, actual_rows = actual
        expected_header, expected_rows = expected
        details.update(actual_rows=len(actual_rows), expected_rows=len(expected_rows), format="csv")
        if actual_header != expected_header:
            return fail("schema_mismatch", "CSV columns or column order differ from the expected schema")
        if len(actual_rows) != len(expected_rows):
            return fail(
                "row_count_mismatch",
                f"CSV has {len(actual_rows)} data rows; expected {len(expected_rows)}",
            )
        for row_index, (actual_row, expected_row) in enumerate(zip(actual_rows, expected_rows), start=2):
            for column, left, right in zip(expected_header, actual_row, expected_row):
                if left == right:
                    continue
                if not _IDENTIFIER.search(column) and _numeric_equal(left, right):
                    continue
                details.update(row=row_index, column=column[:160])
                return fail("value_mismatch", f"CSV row {row_index}, column {column[:80]} differs")
    else:
        details.update(format="json", array_order="significant", object_key_order="ignored")
        try:
            difference = _json_difference(actual, expected)
        except RecursionError:
            return fail("invalid_output", "JSON nesting exceeds the evaluator limit")
        if difference:
            return fail("value_mismatch", difference)
    return {"passed": True, "error": None, "details": {**details, "code": "passed"}}

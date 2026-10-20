from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.demo import BUSINESS_CORRECTION, build_demo
from backend.app.evaluator import MAX_ARTIFACT_BYTES, evaluate_output, validate_case


def grade(tmp_path: Path, actual: str, expected: str, name: str = "clean.csv") -> dict:
    path = tmp_path / name
    path.write_text(actual, encoding="utf-8")
    return evaluate_output([{"name": name, "path": str(path), "size": path.stat().st_size}], name, expected)


def test_csv_semantic_number_and_unicode(tmp_path):
    expected = "email,name,amount_usd\na@example.com,Renée 李,12.30\n"
    actual = "email,name,amount_usd\r\na@example.com,Renée 李,12.3000001\r\n"
    assert grade(tmp_path, actual, expected)["passed"]


@pytest.mark.parametrize(
    ("actual", "code"),
    [
        ("name,email,amount_usd\nRenée,a@example.com,12.30\n", "schema_mismatch"),
        ("email,name,amount_usd\na@example.com,Renée,12.30\na@example.com,Renée,12.30\n", "row_count_mismatch"),
        ("email,name,amount_usd\na@example.com, Renée,12.30\n", "value_mismatch"),
        ("email,name,amount_usd\na@example.com,Renée,12.31\n", "value_mismatch"),
        ("email,name,amount_usd\na@example.com,Renée\n", "invalid_output"),
        ('email,name,amount_usd\na@example.com,"Renée,12.30\n', "invalid_output"),
        ("email,email,amount_usd\na@example.com,Renée,12.30\n", "invalid_output"),
    ],
)
def test_bad_csv_fails(tmp_path, actual, code):
    result = grade(tmp_path, actual, "email,name,amount_usd\na@example.com,Renée,12.30\n")
    assert not result["passed"]
    assert result["details"]["code"] == code


def test_rows_are_ordered_and_identifiers_are_exact(tmp_path):
    assert not grade(tmp_path, "id,value\n2,4\n1,3\n", "id,value\n1,3\n2,4\n")["passed"]
    assert not grade(tmp_path, "customer_id,value\n1.0,3\n", "customer_id,value\n1,3\n")["passed"]
    assert not grade(tmp_path, "label,value\n001,3\n", "label,value\n1,3\n")["passed"]


def test_json_is_semantic_but_types_and_array_order_matter(tmp_path):
    expected = '{"people":[{"name":"Renée", "amount":12.3}],"ok":true}'
    assert grade(tmp_path, '{"ok":true,"people":[{"amount":12.3000001,"name":"Renée"}]}', expected, "result.json")["passed"]
    assert not grade(tmp_path, '{"ok":1,"people":[{"amount":12.3,"name":"Renée"}]}', expected, "result.json")["passed"]
    assert not grade(tmp_path, "[2,1]", "[1,2]", "result.json")["passed"]


@pytest.mark.parametrize("actual", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":'])
def test_malformed_json_is_rejected(tmp_path, actual):
    result = grade(tmp_path, actual, '{"a":2}', "result.json")
    assert result["details"]["code"] == "invalid_output"


def test_missing_duplicate_and_unsafe_artifact_names(tmp_path):
    expected = "name\nAlice\n"
    assert evaluate_output([], "clean.csv", expected)["details"]["code"] == "missing_artifact"
    path = tmp_path / "clean.csv"
    path.write_text(expected)
    artifact = {"name": "clean.csv", "path": str(path)}
    assert evaluate_output([artifact, artifact], "clean.csv", expected)["details"]["code"] == "duplicate_artifact"
    for name in ["../clean.csv", "/clean.csv", "dir/clean.csv", "dir\\clean.csv", "C:clean.csv", "clean\x00.csv"]:
        assert evaluate_output([{**artifact, "name": name}], "clean.csv", expected)["details"]["code"] == "unsafe_artifact"


def test_symlink_and_size_are_checked_on_disk(tmp_path):
    expected = "name\nAlice\n"
    target = tmp_path / "target.csv"
    target.write_text(expected)
    link = tmp_path / "clean.csv"
    link.symlink_to(target)
    result = evaluate_output([{"name": "clean.csv", "path": str(link), "size": 1}], "clean.csv", expected)
    assert not result["passed"]
    assert "link" in result["error"].lower()
    link.unlink()
    link.write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    result = evaluate_output([{"name": "clean.csv", "path": str(link), "size": 1}], "clean.csv", expected)
    assert not result["passed"]
    assert "limit" in result["error"]


def test_directory_and_non_utf8_are_rejected(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    assert not evaluate_output([{"name": "clean.csv", "path": str(output)}], "clean.csv", "a\n1\n")["passed"]
    output = tmp_path / "clean.csv"
    output.write_bytes(b"a\n\xff\n")
    assert not evaluate_output([{"name": "clean.csv", "path": str(output)}], "clean.csv", "a\n1\n")["passed"]


def test_invalid_expectation_is_distinct(tmp_path):
    result = evaluate_output([], "../clean.csv", "a\n1\n")
    assert result["details"]["code"] == "invalid_expectation"
    with pytest.raises(ValueError):
        validate_case({"expected_name": "clean.csv", "expected_content": "a,a\n1,2\n"})


def test_extreme_numeric_exponents_fail_closed(tmp_path):
    assert not grade(tmp_path, "amount\n1e999999999\n", "amount\n1\n")["passed"]
    assert not grade(tmp_path, '{"amount":1e999999999}', '{"amount":1}', "output.json")["passed"]


def test_demo_expectations_are_valid_and_duplicates_fail(tmp_path):
    demo = build_demo()
    assert len(demo["cases"]) == 8
    assert [case["split"] for case in demo["cases"]].count("holdout") == 2
    for case in demo["cases"]:
        validate_case(case)
        result = grade(tmp_path, case["expected_content"], case["expected_content"])
        assert result["passed"], case["name"]
        assert json.dumps(result)
    case = demo["cases"][0]
    duplicate_row = case["expected_content"].splitlines()[-1]
    assert not grade(tmp_path, case["expected_content"] + duplicate_row + "\n", case["expected_content"])["passed"]
    assert case["correction"] == BUSINESS_CORRECTION
    assert "newly supplied" in BUSINESS_CORRECTION


def test_demo_expected_files_follow_documented_policy():
    """Independently check authored expectations against the supplied rules."""
    for case in build_demo()["cases"]:
        normalized = []
        for source_row in csv.DictReader(io.StringIO(case["files"][0]["content"])):
            row = {key: value.strip() for key, value in source_row.items()}
            row["email"] = row["email"].lower()
            raw_date = row["updated_at"]
            parsed = datetime.strptime(raw_date, "%m/%d/%Y") if "/" in raw_date else datetime.fromisoformat(raw_date)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            row["updated_at"] = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            amount = row["amount_usd"].replace("$", "").replace(",", "")
            row["amount_usd"] = f"{Decimal(amount):.2f}" if amount else ""
            normalized.append(row)
        winners = {}
        for index, row in enumerate(normalized):
            previous_index = winners.get(row["email"])
            if previous_index is None or row["updated_at"] >= normalized[previous_index]["updated_at"]:
                winners[row["email"]] = index
        actual_rows = [row for index, row in enumerate(normalized) if winners[row["email"]] == index]
        expected_rows = list(csv.DictReader(io.StringIO(case["expected_content"])))
        assert actual_rows == expected_rows, case["name"]

"""A small, explicit business-policy adaptation demo, with no fake agent runs.

The initial agent cleans exports and preserves records by default. Reviewed
feedback introduces the team's policy: one customer per normalized email, newest
updated_at wins, equal timestamps choose the last source row. The expected files
below encode that new policy. A pre-policy failure is adaptation evidence, not a
claim that the model should have guessed an unstated requirement.

Train cases may be shown to the improver. Validation cases are selection data;
holdout cases must be reserved for an explicit final report, never the gate.
"""

from __future__ import annotations

import csv
import io


_COLUMNS = ["email", "name", "updated_at", "amount_usd"]

BASE_INSTRUCTIONS = """You clean customer data exports for an operations team.
Use the available file and code-execution tools to inspect the input, transform
it, and write a real clean.csv artifact. Do not merely describe a transformation.

The canonical CSV columns, in this order, are email,name,updated_at,amount_usd.
Trim leading/trailing whitespace from all fields and lowercase email addresses.
Preserve Unicode names and the spelling of names after trimming. Parse ISO-8601
dates/timestamps and US MM/DD/YYYY dates; date-only values mean midnight UTC.
Write timestamps in UTC as YYYY-MM-DDTHH:MM:SSZ. Normalize USD amounts by removing
currency signs and thousands separators and writing exactly two decimal places;
an empty amount stays empty. Use the Python standard library if possible.

Follow explicit task requirements and learned business rules. Otherwise preserve
all source records and their original relative order; do not invent a duplicate
or missing-value policy. When filtering records, preserve the surviving records'
original relative order unless a task or learned rule specifies another order.
Before finishing, read the output back and check its schema and values. Return a
short summary and the output filename. Nobody can answer questions during a run:
if an unsupported date or conflicting rule makes the task ambiguous, choose the
most conservative interpretation, state that assumption in the summary, and
still write the output file.
"""

BUSINESS_CORRECTION = (
    "Our customer exports must contain one record per normalized email. "
    "After trimming and lowercasing emails, retain the record with the newest "
    "updated_at timestamp. If timestamps tie, retain the last row in the source "
    "file. Preserve the surviving rows' original relative order. Keep the "
    "existing schema, date, currency, and Unicode normalization rules. This is "
    "a newly supplied business policy; the previous default preserved all rows."
)

DEFAULT_PROMPT = "Clean customers.csv for the operations team and write clean.csv."


def _csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def _case(name: str, source: list[list[str]], expected: list[list[str]], split: str) -> dict:
    case = {
        "name": name,
        "prompt": DEFAULT_PROMPT,
        "files": [{"name": "customers.csv", "content": _csv(source)}],
        "expected_name": "clean.csv",
        "expected_content": _csv(expected),
        "split": split,
        "tags": ["customer-normalization", "business-policy-adaptation"],
    }
    if split == "train":
        case["correction"] = BUSINESS_CORRECTION
    return case


def build_demo() -> dict:
    cases = [
        _case(
            "Newest customer record",
            [
                [" Alice@Example.com ", " Alice Old ", "10/01/2026", "$100.00"],
                ["bob@example.com", " Bob ", "2026-10-02", "25"],
                ["alice@example.com", " Alice Chen ", "2026-10-05", "$120.50"],
            ],
            [
                ["bob@example.com", "Bob", "2026-10-02T00:00:00Z", "25.00"],
                ["alice@example.com", "Alice Chen", "2026-10-05T00:00:00Z", "120.50"],
            ],
            "train",
        ),
        _case(
            "Newer record appears first",
            [
                ["jules@example.com", " Jules Rivera ", "2026-10-10T14:00:00Z", "$1,250.75"],
                ["JULES@example.com ", "Jules Old", "2026-10-08", "900"],
                ["mia@example.com", " Mia ", "10/09/2026", ""],
            ],
            [
                ["jules@example.com", "Jules Rivera", "2026-10-10T14:00:00Z", "1250.75"],
                ["mia@example.com", "Mia", "2026-10-09T00:00:00Z", ""],
            ],
            "train",
        ),
        _case(
            "Equal timestamps use last source row",
            [
                [" lee@example.com", "Lee Draft", "2026-10-11T09:00:00Z", "10"],
                ["sam@example.com", " Sam ", "10/10/2026", "$15.40"],
                ["LEE@example.com", " Lee Final ", "2026-10-11T09:00:00Z", "12"],
            ],
            [
                ["sam@example.com", "Sam", "2026-10-10T00:00:00Z", "15.40"],
                ["lee@example.com", "Lee Final", "2026-10-11T09:00:00Z", "12.00"],
            ],
            "train",
        ),
        _case(
            "Unique records preserve order",
            [
                [" ZOE@Example.com ", " Zoë Park ", "10/12/2026", "$2,010.05"],
                ["ann@example.com", " Ann ", "2026-10-01T06:30:00Z", "0"],
            ],
            [
                ["zoe@example.com", "Zoë Park", "2026-10-12T00:00:00Z", "2010.05"],
                ["ann@example.com", "Ann", "2026-10-01T06:30:00Z", "0.00"],
            ],
            "validation",
        ),
        _case(
            "Interleaved duplicate groups",
            [
                ["a@example.com", "A New", "2026-10-15", "8.5"],
                ["b@example.com", "B Old", "2026-10-01", "5"],
                [" A@EXAMPLE.COM ", "A Old", "2026-10-02", "7"],
                ["B@example.com", " B New ", "2026-10-16", "6"],
            ],
            [
                ["a@example.com", "A New", "2026-10-15T00:00:00Z", "8.50"],
                ["b@example.com", "B New", "2026-10-16T00:00:00Z", "6.00"],
            ],
            "validation",
        ),
        _case(
            "UTC comparisons preserve Unicode",
            [
                ["renee@example.com", "Renée Old", "2026-10-15T10:00:00+02:00", "40"],
                ["RENee@example.com", " Renée 李 ", "2026-10-15T08:30:00Z", "$42.25"],
                ["yuki@example.com", " ゆき ", "2026-10-14", ""],
            ],
            [
                ["renee@example.com", "Renée 李", "2026-10-15T08:30:00Z", "42.25"],
                ["yuki@example.com", "ゆき", "2026-10-14T00:00:00Z", ""],
            ],
            "validation",
        ),
        _case(
            "Final audit: nonadjacent updates",
            [
                ["noor@example.com", "Noor Old", "2026-10-17", "12"],
                ["elliot@example.com", " Elliot New ", "2026-10-19T01:00:00Z", "$150.25"],
                ["NOOR@example.com", " Noor New ", "2026-10-18", "$18.00"],
                ["elliot@example.com", "Elliot Old", "2026-10-18T23:00:00Z", "100"],
                ["aya@example.com", " Aya ", "10/16/2026", "0"],
            ],
            [
                ["elliot@example.com", "Elliot New", "2026-10-19T01:00:00Z", "150.25"],
                ["noor@example.com", "Noor New", "2026-10-18T00:00:00Z", "18.00"],
                ["aya@example.com", "Aya", "2026-10-16T00:00:00Z", "0.00"],
            ],
            "holdout",
        ),
        _case(
            "Final audit: already canonical",
            [
                ["zara@example.com", "Zara", "2026-10-19T12:15:00Z", "32.80"],
                ["alex@example.com", "Alex", "2026-10-19T13:00:00Z", ""],
            ],
            [
                ["zara@example.com", "Zara", "2026-10-19T12:15:00Z", "32.80"],
                ["alex@example.com", "Alex", "2026-10-19T13:00:00Z", ""],
            ],
            "holdout",
        ),
    ]
    return {
        "name": "Customer Export Assistant",
        "description": (
            "Clean CSV customer exports with code and inspect every step. "
            "Use reviewed corrections to teach the team's newest-record policy, "
            "then measure the change before publishing it."
        ),
        "instructions": BASE_INSTRUCTIONS,
        "prompt": DEFAULT_PROMPT,
        "files": cases[0]["files"],
        "correction": BUSINESS_CORRECTION,
        "cases": cases,
    }

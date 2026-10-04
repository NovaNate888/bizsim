"""
Standalone check of utils.exam_export.build_exam_export (no Flask, DB or R2).

Builds a combined export for 3 hypothetical students, writes it to
tests/sample_exam_export.csv, prints it, and asserts the column/quoting rules.

Run with:
    cd bizsim
    py tests/verify_exam_export.py
"""
import csv
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.exam_export import build_exam_export

HEADER = ["question_number", "ticker", "question_text", "predicted_answer", "rationale"]
Q1 = "As of 2025-10-24, what is Wall Street's consensus EPS estimate for Waste Management (WM)'s 2025Q3?"
Q6 = "Which business segment drove most of Waste Management (WM)'s revenue growth in 2025Q3?"
Q10 = ('Will WM\'s stock price move in the same direction as its EPS surprise ("aligned") '
       'or in the opposite direction ("divergent") after the 2025Q3 report?')

CAROL_Q6_RATIONALE = (
    'Healthcare Solutions, i.e. the "Stericycle" acquisition, added revenue, but\r\n'
    "organic pricing in Collection & Disposal still mattered."
)


def student_csv(rows) -> bytes:
    """Encode a student file the way the real ones look: UTF-8 with BOM, CRLF."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


STUDENTS = [
    {
        "alias": "alice", "email": "alice@villanova.edu",
        "submitted_at": datetime(2026, 10, 5, 17, 42),
        "rows": [
            ["1", "WM", Q1, "2.01", "Wall Street's consensus EPS estimate for WM for Q3 2025 is $2.01, based on 22 analysts."],
            ["6", "WM", Q6, "Collection & Disposal", "Pricing increases in the core collection business outpaced volume declines."],
            ["10", "WM", Q10, "aligned", "The base rate indicates 62.5% of past quarters were aligned, favoring 'aligned'."],
        ],
    },
    {
        "alias": "bob", "email": "bob@villanova.edu",
        "submitted_at": datetime(2026, 10, 5, 19, 3),
        "rows": [
            ["1", "WM", Q1, "2.10", "Most recent revisions trended up to roughly $2.10 per share."],
            ["6", "WM", Q6, "Recycling", "Higher commodity prices for recycled fiber lifted the recycling line."],
            ["10", "WM", Q10, "divergent", "Guidance cuts have often outweighed EPS beats for WM recently."],
        ],
    },
    {
        "alias": "carol", "email": "carol@villanova.edu",
        "submitted_at": datetime(2026, 10, 6, 1, 15),  # 21:15 EDT on Oct 5
        "rows": [
            ["1", "WM", Q1, "1.98", "Excluding one analyst outlier, the consensus sits near $1.98."],
            ["6", "WM", Q6, "Healthcare Solutions", CAROL_Q6_RATIONALE],
            ["10", "WM", Q10, "aligned", "Strong free cash flow should support a positive reaction to a beat."],
        ],
    },
]


def truncate(value: str, width: int = 30) -> str:
    value = value.replace("\r", "\\r").replace("\n", "\\n")
    return value if len(value) <= width else value[: width - 3] + "..."


def print_table(header, rows) -> None:
    cells = [[truncate(h) for h in header]] + [[truncate(r[h]) for h in header] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(header))]
    sep = "-+-".join("-" * w for w in widths)
    for i, row in enumerate(cells):
        print(" | ".join(c.ljust(w) for c, w in zip(row, widths)))
        if i == 0:
            print(sep)


def main() -> None:
    entries = [
        {
            "course": "MSBAi 8225",
            "section": "001",
            "alias": s["alias"],
            "email": s["email"],
            "submitted_at": s["submitted_at"],
            "csv_bytes": student_csv(s["rows"]),
        }
        for s in STUDENTS
    ]
    out_bytes, ok, errors = build_exam_export(entries)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_exam_export.csv")
    with open(out_path, "wb") as f:
        f.write(out_bytes)

    text = out_bytes.decode("utf-8")
    print("=== Full CSV text ===")
    print(text)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = reader.fieldnames
    rows = list(reader)
    print("=== Readable table ===")
    print_table(header, rows)
    print()
    print(f"students_ok={ok} students_with_errors={errors} -> {out_path}")

    assert not out_bytes.startswith(b"\xef\xbb\xbf"), "output must not have a BOM"
    assert header == [
        "course", "section", "alias", "email", "submission_date",
        "question_number", "ticker", "question_text", "predicted_answer", "rationale",
    ], header
    assert len(rows) == 9, len(rows)
    assert (ok, errors) == (3, 0)
    for r in rows:
        for col in ("course", "section", "alias", "email", "submission_date"):
            assert r[col], f"{col} empty in {r}"
    assert [r["alias"] for r in rows] == ["alice"] * 3 + ["bob"] * 3 + ["carol"] * 3
    assert [r["question_number"] for r in rows] == ["1", "6", "10"] * 3
    assert rows[3]["predicted_answer"] == "2.10", "original text must not be coerced"
    carol = [r for r in rows if r["alias"] == "carol"]
    assert carol[0]["submission_date"].startswith("2026-10-05T21:15"), carol[0]["submission_date"]
    assert carol[0]["submission_date"] == "2026-10-05T21:15:00-04:00"
    assert carol[1]["rationale"] == CAROL_Q6_RATIONALE, repr(carol[1]["rationale"])
    assert carol[1]["rationale"].encode("utf-8") == CAROL_Q6_RATIONALE.encode("utf-8")

    # Error handling: unreadable file and missing required column each yield one row
    bad_bytes, ok2, err2 = build_exam_export([
        {**entries[0], "alias": "dave", "csv_bytes": b"\xff\xfe\x00bad"},
        {**entries[0], "alias": "erin", "csv_bytes": b"question_number,answer\r\n1,2.01\r\n"},
    ])
    bad_rows = list(csv.DictReader(io.StringIO(bad_bytes.decode("utf-8"), newline="")))
    assert (ok2, err2) == (0, 2) and len(bad_rows) == 2
    assert all(r["predicted_answer"].startswith("ERROR: ") for r in bad_rows), bad_rows

    print("All assertions passed.")


if __name__ == "__main__":
    main()

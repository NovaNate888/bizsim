"""
Combined CSV export of exam submissions.

Pure function — no Flask, DB or R2 access — so it can be verified standalone
(see tests/verify_exam_export.py).
"""
import csv
import io

from utils.tz import utc_naive_to_local

IDENTIFIER_COLUMNS = ["course", "section", "alias", "email", "submission_date"]
REQUIRED_COLUMNS = ("question_number", "predicted_answer")
ANSWER_COLUMN = "predicted_answer"


def _parse_student_csv(csv_bytes: bytes) -> tuple[list[str], list[dict]]:
    """Parse one student's file, keeping every value as the original text.
    Raises ValueError with a readable message if it can't be used."""
    if csv_bytes is None:
        raise ValueError("submission file is missing")
    text = csv_bytes.decode("utf-8-sig")
    # newline="" so CR/LF inside quoted fields survive untouched
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = reader.fieldnames
    if not fieldnames:
        raise ValueError("file is empty or has no header row")

    lowered = {(name or "").strip().lower() for name in fieldnames}
    missing = [c for c in REQUIRED_COLUMNS if c not in lowered]
    if missing:
        raise ValueError(f"missing required column(s): {', '.join(missing)}")

    rows = []
    for row in reader:
        extras = row.pop(None, None)
        if extras and any(v.strip() for v in extras):
            raise ValueError(f"line {reader.line_num} has more values than the header")
        rows.append(row)
    return list(fieldnames), rows


def build_exam_export(entries) -> tuple[bytes, int, int]:
    """entries: list of dicts {course, section, alias, email, submitted_at (naive UTC
    datetime), csv_bytes}. An entry may instead carry an "error" string (e.g. the file
    couldn't be downloaded). Returns (csv_bytes, students_ok, students_with_errors)."""
    header = list(IDENTIFIER_COLUMNS)
    seen = set(header)
    out_rows = []
    ok = 0
    errors = 0

    for entry in sorted(entries, key=lambda e: (e.get("alias") or "").lower()):
        local_dt = utc_naive_to_local(entry.get("submitted_at"))
        ids = {
            "course": entry.get("course") or "",
            "section": entry.get("section") or "",
            "alias": entry.get("alias") or "",
            "email": entry.get("email") or "",
            "submission_date": local_dt.isoformat(timespec="seconds") if local_dt else "",
        }

        try:
            if entry.get("error"):
                raise ValueError(entry["error"])
            fieldnames, rows = _parse_student_csv(entry.get("csv_bytes"))
        except Exception as exc:
            out_rows.append({**ids, ANSWER_COLUMN: f"ERROR: {exc}"})
            errors += 1
            continue

        for name in fieldnames:
            if name not in seen:
                seen.add(name)
                header.append(name)
        for row in rows:
            # Identifier columns win if a student file reuses one of their names
            out_rows.append({**row, **ids})
        ok += 1

    if ANSWER_COLUMN not in seen:
        header.append(ANSWER_COLUMN)

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=header, restval="")
    writer.writeheader()
    writer.writerows(out_rows)
    return buf.getvalue().encode("utf-8"), ok, errors

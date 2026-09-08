"""One-time data fix for section due-date overrides stored before the timezone fix.

Every SectionOverride.due_date_override value set before the timezone fix was stored
as exactly what the professor typed, mislabeled as UTC, rather than the correct UTC
equivalent of that Eastern-time wall-clock value. This script corrects those existing
rows in place.

Run this ONCE, locally, against the real database, after deploying the timezone-handling
code changes but before relying on due dates again. Review the printed before/after
values to make sure they look like reasonable Eastern-to-UTC shifts (a few hours later,
not earlier, not multiple days off). Then delete this script so it can't accidentally be
run a second time (running it twice would shift the same dates again and corrupt them).

NOT wired into app.py's _run_schema_migrations — this must stay a manual, one-time step.
"""

from app import create_app
from models import db, SectionOverride
from utils.tz import local_input_to_utc_naive

app = create_app()
with app.app_context():
    overrides = SectionOverride.query.filter(
        SectionOverride.has_due_date_override == True,
        SectionOverride.due_date_override.isnot(None),
    ).all()
    print(f"Found {len(overrides)} due date override(s) to fix.")
    for o in overrides:
        old = o.due_date_override
        new = local_input_to_utc_naive(old)
        print(f"  section_id={o.section_id} assignment_id={o.assignment_id}: {old} -> {new}")
        o.due_date_override = new
    db.session.commit()
    print("Done.")

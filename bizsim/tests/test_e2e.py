"""
End-to-end test for BizSim.

Covers the full lifecycle:
  1.  Create an instructor account (seeded directly — no public registration for instructors)
  2.  Instructor logs in and sets up Fall 2026 / ML 3200 / Section 002
  3.  Instructor creates an assignment with a dataset CSV and ground truth CSV
  4.  Student registers, email is verified, and they are enrolled in the section
  5.  Student downloads the dataset
  6.  Student generates a perfect-score submission CSV and uploads it
  7.  Score is computed (RMSE = 0.0) and persisted
  8.  Student's alias appears #1 on the leaderboard
  9.  Instructor leaderboard shows alias + real email + score

Run with:
    cd bizsim
    py -m pytest tests/test_e2e.py -v
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

import pytest
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from models import db as _db, Instructor, User
from utils.email import generate_verification_token


# ---------------------------------------------------------------------------
# Synthetic CSV data
# ---------------------------------------------------------------------------

DATASET_CSV = b"id,sqft,bedrooms,bathrooms\n1,1200,3,2\n2,1500,4,3\n3,900,2,1\n"
GROUND_TRUTH_CSV = b"id,price\n1,300000\n2,400000\n3,250000\n"
PERFECT_SUBMISSION_CSV = b"id,price\n1,300000\n2,400000\n3,250000\n"   # RMSE = 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """
    Isolated Flask app backed by an in-memory SQLite database.

    StaticPool forces all SQLAlchemy connections to reuse the same
    underlying connection, so every app-context sees the same in-memory
    database state (important for fixtures that run in separate contexts).
    """
    upload_dir = tempfile.mkdtemp()
    gt_dir = tempfile.mkdtemp()
    dataset_dir = tempfile.mkdtemp()

    application = create_app("development")
    application.config.update(
        TESTING=True,
        DEBUG=True,              # activates dev-mode email shortcut (no SMTP)
        SECRET_KEY="test-secret-key",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        },
        UPLOAD_FOLDER=upload_dir,
        GROUND_TRUTH_FOLDER=gt_dir,
        DATASET_FOLDER=dataset_dir,
        MAIL_SUPPRESS_SEND=True,
    )

    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def instructor_info(app):
    """Seed a verified instructor account directly in the DB."""
    with app.app_context():
        user = User(
            email="prof.smith@university.edu",
            is_verified=True,
            is_instructor=True,
            alias="ProfSmith",
        )
        user.set_password("ProfPass123!")
        _db.session.add(user)
        _db.session.flush()

        instr = Instructor(
            user_id=user.id,
            name="Prof. Smith",
            department="Computer Science",
        )
        _db.session.add(instr)
        _db.session.commit()

    return {"email": "prof.smith@university.edu", "password": "ProfPass123!"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login(client, email, password):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def logout(client):
    client.get("/auth/logout", follow_redirects=True)


def text(r) -> str:
    return r.data.decode()


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def test_full_e2e(app, client, instructor_info):

    # ------------------------------------------------------------------ #
    # STEP 1 – Instructor logs in                                          #
    # ------------------------------------------------------------------ #
    r = login(client, instructor_info["email"], instructor_info["password"])
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    assert "Instructor Panel" in text(r), "Expected instructor dashboard after login"

    # ------------------------------------------------------------------ #
    # STEP 2a – Create semester "Fall 2026"                                #
    # ------------------------------------------------------------------ #
    r = client.post(
        "/instructor/semesters",
        data={"action": "create", "name": "Fall 2026"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Fall 2026" in text(r), "Semester not visible on page after creation"

    with app.app_context():
        from models import Semester
        sem = Semester.query.filter_by(name="Fall 2026").first()
        assert sem is not None, "Semester not found in DB"
        sem_id = sem.id

    # ------------------------------------------------------------------ #
    # STEP 2b – Create course "Machine Learning / ML 3200"                 #
    # ------------------------------------------------------------------ #
    r = client.post(
        "/instructor/courses",
        data={
            "action": "create",
            "name": "Machine Learning",
            "code": "ML 3200",
            "description": "Intro to machine learning.",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Machine Learning" in text(r), "Course not visible on page after creation"

    with app.app_context():
        from models import Course
        course = Course.query.filter_by(name="Machine Learning").first()
        assert course is not None, "Course not found in DB"
        course_id = course.id

    # ------------------------------------------------------------------ #
    # STEP 2c – Create section 002                                         #
    # ------------------------------------------------------------------ #
    r = client.post(
        f"/instructor/courses/{course_id}/sections",
        data={"action": "create", "semester_id": sem_id, "section_name": "002"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "002" in text(r), "Section not visible on page after creation"

    with app.app_context():
        from models import Section
        section = Section.query.filter_by(section_name="002", course_id=course_id).first()
        assert section is not None, "Section not found in DB"
        section_id = section.id

    # ------------------------------------------------------------------ #
    # STEP 3 – Create assignment with dataset + ground truth CSVs          #
    # ------------------------------------------------------------------ #
    r = client.post(
        f"/instructor/section/{section_id}/assignments/new",
        content_type="multipart/form-data",
        data={
            "title": "House Price Prediction",
            "description": "Predict sale prices for residential homes.",
            "scoring_metric": "rmse",
            "target_column": "price",
            "max_submissions_per_day": "10",
            "due_date": "2026-12-15T23:59",
            "dataset_file": (io.BytesIO(DATASET_CSV), "train.csv", "text/csv"),
            "ground_truth_file": (io.BytesIO(GROUND_TRUTH_CSV), "gt.csv", "text/csv"),
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "House Price Prediction" in text(r), f"Assignment not on page. Body:\n{text(r)[:500]}"

    with app.app_context():
        from models import Assignment
        assignment = Assignment.query.filter_by(title="House Price Prediction").first()
        assert assignment is not None, "Assignment not found in DB"
        assert assignment.dataset_filename, "Dataset file was not saved"
        assert assignment.ground_truth_filename, "Ground truth file was not saved"
        assert assignment.target_column == "price"
        assert assignment.scoring_metric == "rmse"
        # Verify the ground truth file is actually on disk
        gt_path = os.path.join(app.config["GROUND_TRUTH_FOLDER"], assignment.ground_truth_filename)
        assert os.path.exists(gt_path), "Ground truth file missing from disk"
        dataset_path = os.path.join(app.config["DATASET_FOLDER"], assignment.dataset_filename)
        assert os.path.exists(dataset_path), "Dataset file missing from disk"
        assignment_id = assignment.id

    # ------------------------------------------------------------------ #
    # STEP 4 – Student registers and is auto-enrolled in section 002       #
    # ------------------------------------------------------------------ #
    logout(client)

    r = client.post(
        "/auth/register",
        data={
            "email": "student1@university.edu",
            "password": "StudentPass123!",
            "confirm_password": "StudentPass123!",
            "section_id": str(section_id),
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Account created" in text(r), f"Registration failed. Body:\n{text(r)[:500]}"

    # Grab the auto-generated alias before verification
    with app.app_context():
        from models import Enrollment
        student = User.query.filter_by(email="student1@university.edu").first()
        assert student is not None, "Student not found in DB after registration"
        assert not student.is_verified, "Student should not be verified yet"
        assert student.alias, "Auto-generated alias is missing"
        student_alias = student.alias

        enrollment = Enrollment.query.filter_by(
            user_id=student.id, section_id=section_id
        ).first()
        assert enrollment is not None, "Student not enrolled in the section"

        token = generate_verification_token("student1@university.edu")

    # Verify via email link
    r = client.get(f"/auth/verify/{token}", follow_redirects=True)
    assert r.status_code == 200
    assert "verified" in text(r).lower(), f"Expected verification success. Body:\n{text(r)[:300]}"

    with app.app_context():
        student = User.query.filter_by(email="student1@university.edu").first()
        assert student.is_verified, "Student still unverified after clicking link"

    # ------------------------------------------------------------------ #
    # STEP 5 – Student logs in and downloads the dataset                   #
    # ------------------------------------------------------------------ #
    r = login(client, "student1@university.edu", "StudentPass123!")
    assert r.status_code == 200
    assert "Dashboard" in text(r), f"Expected student dashboard. Body:\n{text(r)[:300]}"

    r = client.get(f"/student/assignment/{assignment_id}/download-dataset")
    assert r.status_code == 200, f"Dataset download returned {r.status_code}"
    assert b"sqft" in r.data, "Downloaded file doesn't look like the dataset CSV"
    assert r.data == DATASET_CSV, "Downloaded CSV content doesn't match what was uploaded"

    # ------------------------------------------------------------------ #
    # STEP 6 – Student uploads a perfect submission (RMSE = 0)             #
    # ------------------------------------------------------------------ #
    r = client.post(
        f"/student/section/{section_id}/assignment/{assignment_id}",
        content_type="multipart/form-data",
        data={
            "submission_file": (
                io.BytesIO(PERFECT_SUBMISSION_CSV),
                "submission.csv",
                "text/csv",
            ),
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    body = text(r)
    assert "Submission received" in body, f"Expected success flash. Body:\n{body[:500]}"
    assert "0.0000" in body, f"Expected RMSE score of 0.0000. Body:\n{body[:500]}"

    # ------------------------------------------------------------------ #
    # STEP 7 – Submission + score persisted in DB                          #
    # ------------------------------------------------------------------ #
    with app.app_context():
        from models import Submission
        sub = (
            Submission.query
            .join(User)
            .filter(
                User.email == "student1@university.edu",
                Submission.assignment_id == assignment_id,
            )
            .first()
        )
        assert sub is not None, "Submission not found in DB"
        assert sub.score is not None, "Score was not computed"
        assert abs(sub.score) < 1e-6, f"Expected RMSE ≈ 0.0, got {sub.score}"
        assert sub.error_message is None, f"Unexpected scoring error: {sub.error_message}"

        # Submission file exists on disk
        sub_path = os.path.join(app.config["UPLOAD_FOLDER"], sub.filename)
        assert os.path.exists(sub_path), "Submission CSV not saved to disk"

    # ------------------------------------------------------------------ #
    # STEP 8 – Student alias is #1 on the student-facing leaderboard       #
    # ------------------------------------------------------------------ #
    r = client.get(
        f"/student/section/{section_id}/assignment/{assignment_id}/leaderboard"
    )
    assert r.status_code == 200
    body = text(r)
    assert student_alias in body, f"Alias '{student_alias}' not found on leaderboard"
    assert "0.0000" in body, "Score not shown on leaderboard"
    # With one student, the gold medal must be somewhere on the page
    assert "🥇" in body, "Gold medal (rank 1) not found on leaderboard"
    # The alias and medal should both be in the table body (tbody), not just the navbar
    tbody_start = body.find("<tbody>")
    tbody_end = body.find("</tbody>")
    assert tbody_start != -1 and tbody_end != -1, "Could not find table body on leaderboard"
    tbody = body[tbody_start:tbody_end]
    assert student_alias in tbody, "Alias not found inside leaderboard table"
    assert "🥇" in tbody, "Gold medal not found inside leaderboard table"

    # ------------------------------------------------------------------ #
    # STEP 9 – Instructor leaderboard shows alias + email + score          #
    # ------------------------------------------------------------------ #
    logout(client)
    login(client, instructor_info["email"], instructor_info["password"])

    r = client.get(f"/instructor/assignment/{assignment_id}/leaderboard")
    assert r.status_code == 200
    body = text(r)
    assert "student1@university.edu" in body, "Email missing from instructor leaderboard"
    assert "0.0000" in body, "Score missing from instructor leaderboard"
    tbody_start = body.find("<tbody>")
    tbody_end = body.find("</tbody>")
    tbody = body[tbody_start:tbody_end]
    assert student_alias in tbody, "Alias not found in instructor leaderboard table"
    assert "🥇" in tbody, "Rank 1 medal missing from instructor leaderboard table"

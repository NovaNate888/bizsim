import io
import uuid
from datetime import datetime, timezone

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from models import Assignment, Enrollment, Section, Submission, db
from utils import storage
from utils.scoring import score_from_streams

from . import student_bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in current_app.config["ALLOWED_EXTENSIONS"]
    )


def _get_enrollment_or_404(user_id: int, section_id: int) -> Enrollment:
    enrollment = Enrollment.query.filter_by(
        user_id=user_id, section_id=section_id
    ).first_or_404()
    return enrollment


def _require_verified(user):
    if not user.is_verified:
        flash("Please verify your email before accessing this page.", "warning")
        return redirect(url_for("auth.login"))
    return None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@student_bp.route("/dashboard")
@login_required
def dashboard():
    redir = _require_verified(current_user)
    if redir:
        return redir

    # All sections the student is enrolled in
    enrollments = current_user.enrollments.all()

    # For each enrollment, get open assignments and submission counts
    enrollment_data = []
    for enr in enrollments:
        section = enr.section
        assignments = (
            section.assignments
            .filter_by(is_active=True)
            .order_by(Assignment.due_date.asc().nullslast())
            .all()
        )
        sub_count = current_user.submissions.filter(
            Submission.assignment_id.in_([a.id for a in assignments])
        ).count()
        enrollment_data.append(
            {"enrollment": enr, "section": section, "assignments": assignments, "submission_count": sub_count}
        )

    return render_template(
        "student/dashboard.html",
        enrollment_data=enrollment_data,
    )


# ---------------------------------------------------------------------------
# Assignments list for a section
# ---------------------------------------------------------------------------

@student_bp.route("/section/<int:section_id>/assignments")
@login_required
def assignments(section_id: int):
    redir = _require_verified(current_user)
    if redir:
        return redir

    enrollment = Enrollment.query.filter_by(
        user_id=current_user.id, section_id=section_id
    ).first_or_404()
    section = enrollment.section
    assignments_list = (
        section.assignments
        .filter_by(is_active=True)
        .order_by(Assignment.due_date.asc().nullslast())
        .all()
    )

    # Map assignment_id -> best score for current user
    best_scores = {}
    for assignment in assignments_list:
        best = _best_submission(current_user.id, assignment)
        best_scores[assignment.id] = best

    return render_template(
        "student/assignments.html",
        section=section,
        assignments=assignments_list,
        best_scores=best_scores,
    )


# ---------------------------------------------------------------------------
# Assignment detail + submit
# ---------------------------------------------------------------------------

@student_bp.route(
    "/section/<int:section_id>/assignment/<int:assignment_id>",
    methods=["GET", "POST"],
)
@login_required
def assignment_detail(section_id: int, assignment_id: int):
    redir = _require_verified(current_user)
    if redir:
        return redir

    enrollment = Enrollment.query.filter_by(
        user_id=current_user.id, section_id=section_id
    ).first_or_404()
    section = enrollment.section
    assignment = Assignment.query.get_or_404(assignment_id)

    if assignment.section_id != section_id:
        abort(404)

    # Submission history for this user + assignment
    history = (
        Submission.query
        .filter_by(user_id=current_user.id, assignment_id=assignment_id)
        .order_by(Submission.submitted_at.desc())
        .all()
    )

    today_count = _submissions_today(current_user.id, assignment_id)
    can_submit = (
        not assignment.is_past_due()
        and today_count < assignment.max_submissions_per_day
    )

    if request.method == "POST":
        if not can_submit:
            if assignment.is_past_due():
                flash("This assignment is past due.", "danger")
            else:
                flash(
                    f"You have reached the daily submission limit "
                    f"({assignment.max_submissions_per_day} per day).",
                    "warning",
                )
            return redirect(
                url_for(
                    "student.assignment_detail",
                    section_id=section_id,
                    assignment_id=assignment_id,
                )
            )

        file = request.files.get("submission_file")
        if not file or file.filename == "":
            flash("Please select a CSV file to submit.", "danger")
            return redirect(request.url)

        if not _allowed_file(file.filename):
            flash("Only CSV files are accepted.", "danger")
            return redirect(request.url)

        # Read file bytes once, then upload to R2
        file_bytes = file.read()
        unique_name = f"{uuid.uuid4().hex}.csv"
        r2_key = f"submissions/{assignment_id}/{current_user.id}/{unique_name}"
        storage.upload_fileobj(io.BytesIO(file_bytes), r2_key)

        # Score the submission
        score = None
        error_message = None
        if assignment.ground_truth_filename and assignment.target_column:
            try:
                gt_bytes = storage.download_as_bytes(
                    f"ground_truth/{assignment.ground_truth_filename}"
                )
                score = score_from_streams(
                    file_bytes,
                    gt_bytes,
                    assignment.scoring_metric,
                    assignment.target_column,
                )
            except Exception as exc:
                error_message = str(exc)
                current_app.logger.warning(
                    "Scoring error for user %s, assignment %s: %s",
                    current_user.id,
                    assignment_id,
                    exc,
                )
        else:
            error_message = "No ground truth configured; submission stored."

        # R2 key stored in DB
        rel_filename = r2_key

        submission = Submission(
            user_id=current_user.id,
            assignment_id=assignment_id,
            filename=rel_filename,
            score=score,
            error_message=error_message,
        )
        db.session.add(submission)
        db.session.commit()

        if score is not None:
            flash(
                f"Submission received! Your score: "
                f"<strong>{score:.4f}</strong> ({assignment.scoring_metric.upper()})",
                "success",
            )
        elif error_message:
            flash(f"Submission saved, but scoring failed: {error_message}", "warning")
        else:
            flash("Submission received!", "success")

        return redirect(
            url_for(
                "student.assignment_detail",
                section_id=section_id,
                assignment_id=assignment_id,
            )
        )

    best = _best_submission(current_user.id, assignment)
    return render_template(
        "student/assignment_detail.html",
        section=section,
        assignment=assignment,
        history=history,
        today_count=today_count,
        can_submit=can_submit,
        best=best,
    )


# ---------------------------------------------------------------------------
# Dataset download
# ---------------------------------------------------------------------------

@student_bp.route("/assignment/<int:assignment_id>/download-dataset")
@login_required
def download_dataset(assignment_id: int):
    assignment = Assignment.query.get_or_404(assignment_id)

    # Confirm student is enrolled in the assignment's section
    enrollment = Enrollment.query.filter_by(
        user_id=current_user.id, section_id=assignment.section_id
    ).first_or_404()

    if not assignment.dataset_filename:
        flash("No dataset has been uploaded for this assignment yet.", "warning")
        return redirect(url_for("student.dashboard"))

    url = storage.generate_presigned_url(f"datasets/{assignment.dataset_filename}")
    return redirect(url)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

@student_bp.route("/section/<int:section_id>/assignment/<int:assignment_id>/leaderboard")
@login_required
def leaderboard(section_id: int, assignment_id: int):
    redir = _require_verified(current_user)
    if redir:
        return redir

    enrollment = Enrollment.query.filter_by(
        user_id=current_user.id, section_id=section_id
    ).first_or_404()
    assignment = Assignment.query.get_or_404(assignment_id)

    if assignment.section_id != section_id:
        abort(404)

    section = enrollment.section

    # Get all students enrolled in this section
    enrollments = Enrollment.query.filter_by(section_id=section_id).all()
    user_ids = [e.user_id for e in enrollments]

    # Get best score per student
    from models import User
    board = []
    for uid in user_ids:
        user = User.query.get(uid)
        best = _best_submission(uid, assignment)
        board.append(
            {
                "alias": user.display_name,
                "score": best.score if best else None,
                "submitted_at": best.submitted_at if best else None,
                "is_me": uid == current_user.id,
            }
        )

    # Sort: None scores go to the bottom
    reverse = assignment.higher_is_better
    board.sort(
        key=lambda x: (
            x["score"] is None,
            (-x["score"] if x["score"] is not None and reverse else x["score"]) or 0,
        )
    )

    # Assign ranks (ties share rank)
    _assign_ranks(board, reverse)

    return render_template(
        "student/leaderboard.html",
        section=section,
        assignment=assignment,
        board=board,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _best_submission(user_id: int, assignment: Assignment):
    """Return the best-scoring Submission for this user+assignment, or None."""
    subs = (
        Submission.query
        .filter_by(user_id=user_id, assignment_id=assignment.id)
        .filter(Submission.score.isnot(None))
        .all()
    )
    if not subs:
        return None
    if assignment.higher_is_better:
        return max(subs, key=lambda s: s.score)
    return min(subs, key=lambda s: s.score)


def _submissions_today(user_id: int, assignment_id: int) -> int:
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        Submission.query
        .filter(
            Submission.user_id == user_id,
            Submission.assignment_id == assignment_id,
            Submission.submitted_at >= today_start,
        )
        .count()
    )


def _assign_ranks(board: list, reverse: bool) -> None:
    rank = 1
    for i, entry in enumerate(board):
        if entry["score"] is None:
            entry["rank"] = "—"
            continue
        if i == 0:
            entry["rank"] = rank
        else:
            prev = board[i - 1]
            if prev["score"] == entry["score"]:
                entry["rank"] = prev["rank"]
            else:
                rank = i + 1
                entry["rank"] = rank

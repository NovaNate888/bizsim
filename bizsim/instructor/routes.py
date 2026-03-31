import io
import os
import uuid
from datetime import datetime
from functools import wraps

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
from utils import storage

from models import (
    Assignment,
    Course,
    Enrollment,
    Instructor,
    METRIC_CHOICES,
    Section,
    Semester,
    Submission,
    User,
    db,
)

from . import instructor_bp


# ---------------------------------------------------------------------------
# Access guard
# ---------------------------------------------------------------------------

def instructor_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not (current_user.is_instructor or current_user.is_admin):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _get_instructor_or_404() -> Instructor:
    instr = Instructor.query.filter_by(user_id=current_user.id).first()
    if not instr and not current_user.is_admin:
        abort(403)
    return instr


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@instructor_bp.route("/dashboard")
@login_required
@instructor_required
def dashboard():
    instr = _get_instructor_or_404()
    courses = instr.courses.filter_by(is_active=True).all() if instr else []
    total_students = (
        Enrollment.query
        .join(Section)
        .join(Course)
        .filter(Course.instructor_id == instr.id)
        .count()
        if instr
        else 0
    )
    total_submissions = (
        Submission.query
        .join(Assignment)
        .join(Section)
        .join(Course)
        .filter(Course.instructor_id == instr.id)
        .count()
        if instr
        else 0
    )
    return render_template(
        "instructor/dashboard.html",
        instructor=instr,
        courses=courses,
        total_students=total_students,
        total_submissions=total_submissions,
    )


# ---------------------------------------------------------------------------
# Semesters
# ---------------------------------------------------------------------------

@instructor_bp.route("/semesters", methods=["GET", "POST"])
@login_required
@instructor_required
def semesters():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Semester name is required.", "danger")
            elif Semester.query.filter_by(name=name).first():
                flash("A semester with that name already exists.", "warning")
            else:
                db.session.add(Semester(name=name))
                db.session.commit()
                flash(f"Semester '{name}' created.", "success")

        elif action == "toggle":
            sem_id = request.form.get("semester_id", type=int)
            sem = Semester.query.get_or_404(sem_id)
            sem.is_active = not sem.is_active
            db.session.commit()
            status = "activated" if sem.is_active else "deactivated"
            flash(f"Semester '{sem.name}' {status}.", "info")

        return redirect(url_for("instructor.semesters"))

    all_semesters = Semester.query.order_by(Semester.id.desc()).all()
    return render_template("instructor/semesters.html", semesters=all_semesters)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@instructor_bp.route("/courses", methods=["GET", "POST"])
@login_required
@instructor_required
def courses():
    instr = _get_instructor_or_404()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form.get("name", "").strip()
            code = request.form.get("code", "").strip()
            description = request.form.get("description", "").strip()
            if not name:
                flash("Course name is required.", "danger")
            else:
                course = Course(
                    instructor_id=instr.id,
                    name=name,
                    code=code or None,
                    description=description or None,
                )
                db.session.add(course)
                db.session.commit()
                flash(f"Course '{name}' created.", "success")

        elif action == "toggle":
            course_id = request.form.get("course_id", type=int)
            course = Course.query.get_or_404(course_id)
            if course.instructor_id != instr.id:
                abort(403)
            course.is_active = not course.is_active
            db.session.commit()
            flash(f"Course '{course.name}' updated.", "info")

        return redirect(url_for("instructor.courses"))

    all_courses = (
        instr.courses.order_by(Course.name.asc()).all() if instr else []
    )
    return render_template("instructor/courses.html", courses=all_courses, instructor=instr)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

@instructor_bp.route("/courses/<int:course_id>/sections", methods=["GET", "POST"])
@login_required
@instructor_required
def sections(course_id: int):
    instr = _get_instructor_or_404()
    course = Course.query.get_or_404(course_id)
    if course.instructor_id != instr.id:
        abort(403)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            semester_id = request.form.get("semester_id", type=int)
            section_name = request.form.get("section_name", "").strip()
            if not semester_id or not section_name:
                flash("Semester and section name are required.", "danger")
            else:
                section = Section(
                    course_id=course.id,
                    semester_id=semester_id,
                    section_name=section_name,
                )
                db.session.add(section)
                db.session.commit()
                flash(f"Section '{section_name}' created.", "success")

        elif action == "toggle":
            section_id = request.form.get("section_id", type=int)
            section = Section.query.get_or_404(section_id)
            section.is_active = not section.is_active
            db.session.commit()
            flash("Section status updated.", "info")

        return redirect(url_for("instructor.sections", course_id=course_id))

    all_sections = (
        course.sections
        .join(Semester)
        .order_by(Semester.name.desc(), Section.section_name.asc())
        .all()
    )
    semesters = Semester.query.filter_by(is_active=True).order_by(Semester.name.asc()).all()
    return render_template(
        "instructor/sections.html",
        course=course,
        sections=all_sections,
        semesters=semesters,
    )


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

@instructor_bp.route("/assignments", methods=["GET"])
@login_required
@instructor_required
def assignments():
    instr = _get_instructor_or_404()
    # Get all sections belonging to this instructor's courses
    section_ids = [
        s.id
        for c in instr.courses.all()
        for s in c.sections.all()
    ]
    all_assignments = (
        Assignment.query
        .filter(Assignment.section_id.in_(section_ids))
        .order_by(Assignment.created_at.desc())
        .all()
    )
    return render_template("instructor/assignments.html", assignments=all_assignments)


@instructor_bp.route("/section/<int:section_id>/assignments/new", methods=["GET", "POST"])
@login_required
@instructor_required
def new_assignment(section_id: int):
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if section.course.instructor_id != instr.id:
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        metric = request.form.get("scoring_metric", "rmse")
        target_col = request.form.get("target_column", "").strip()
        max_subs = request.form.get("max_submissions_per_day", type=int, default=3)
        due_date_str = request.form.get("due_date", "").strip()

        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Invalid due date format.", "danger")
                return render_template(
                    "instructor/new_assignment.html",
                    section=section,
                    metric_choices=METRIC_CHOICES,
                )

        if not title:
            flash("Assignment title is required.", "danger")
            return render_template(
                "instructor/new_assignment.html",
                section=section,
                metric_choices=METRIC_CHOICES,
            )

        assignment = Assignment(
            section_id=section_id,
            title=title,
            description=description or None,
            scoring_metric=metric,
            target_column=target_col or None,
            max_submissions_per_day=max_subs,
            due_date=due_date,
        )
        db.session.add(assignment)
        db.session.flush()

        # Handle dataset upload
        dataset_file = request.files.get("dataset_file")
        if dataset_file and dataset_file.filename:
            if _allowed_file(dataset_file.filename):
                fname = f"assignment_{assignment.id}_{uuid.uuid4().hex}_dataset.csv"
                storage.upload_fileobj(dataset_file.stream, f"datasets/{fname}")
                assignment.dataset_filename = fname
            else:
                flash("Dataset must be a CSV file.", "warning")

        # Handle ground truth upload
        gt_file = request.files.get("ground_truth_file")
        if gt_file and gt_file.filename:
            if _allowed_file(gt_file.filename):
                fname = f"assignment_{assignment.id}_{uuid.uuid4().hex}_gt.csv"
                storage.upload_fileobj(gt_file.stream, f"ground_truth/{fname}")
                assignment.ground_truth_filename = fname
            else:
                flash("Ground truth must be a CSV file.", "warning")

        db.session.commit()
        flash(f"Assignment '{title}' created.", "success")
        return redirect(url_for("instructor.section_detail", section_id=section_id))

    return render_template(
        "instructor/new_assignment.html",
        section=section,
        metric_choices=METRIC_CHOICES,
    )


@instructor_bp.route("/assignment/<int:assignment_id>/edit", methods=["GET", "POST"])
@login_required
@instructor_required
def edit_assignment(assignment_id: int):
    instr = _get_instructor_or_404()
    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.section.course.instructor_id != instr.id:
        abort(403)

    if request.method == "POST":
        assignment.title = request.form.get("title", assignment.title).strip()
        assignment.description = request.form.get("description", "").strip() or None
        assignment.scoring_metric = request.form.get("scoring_metric", assignment.scoring_metric)
        assignment.target_column = request.form.get("target_column", "").strip() or None
        max_subs = request.form.get("max_submissions_per_day", type=int)
        if max_subs:
            assignment.max_submissions_per_day = max_subs

        due_date_str = request.form.get("due_date", "").strip()
        if due_date_str:
            try:
                from datetime import datetime
                assignment.due_date = datetime.strptime(due_date_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Invalid due date.", "danger")
                return render_template(
                    "instructor/edit_assignment.html",
                    assignment=assignment,
                    metric_choices=METRIC_CHOICES,
                )
        else:
            assignment.due_date = None

        assignment.is_active = bool(request.form.get("is_active"))

        # Handle new dataset upload
        dataset_file = request.files.get("dataset_file")
        if dataset_file and dataset_file.filename:
            if _allowed_file(dataset_file.filename):
                fname = f"assignment_{assignment.id}_{uuid.uuid4().hex}_dataset.csv"
                storage.upload_fileobj(dataset_file.stream, f"datasets/{fname}")
                assignment.dataset_filename = fname

        # Handle new ground truth upload
        gt_file = request.files.get("ground_truth_file")
        if gt_file and gt_file.filename:
            if _allowed_file(gt_file.filename):
                fname = f"assignment_{assignment.id}_{uuid.uuid4().hex}_gt.csv"
                storage.upload_fileobj(gt_file.stream, f"ground_truth/{fname}")
                assignment.ground_truth_filename = fname

        db.session.commit()
        flash("Assignment updated.", "success")
        return redirect(
            url_for("instructor.section_detail", section_id=assignment.section_id)
        )

    return render_template(
        "instructor/edit_assignment.html",
        assignment=assignment,
        metric_choices=METRIC_CHOICES,
    )


# ---------------------------------------------------------------------------
# Section detail (students + assignments)
# ---------------------------------------------------------------------------

@instructor_bp.route("/section/<int:section_id>")
@login_required
@instructor_required
def section_detail(section_id: int):
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if section.course.instructor_id != instr.id:
        abort(403)

    enrollments = Enrollment.query.filter_by(section_id=section_id).all()
    assignments_list = (
        section.assignments.order_by(Assignment.due_date.asc().nullslast()).all()
    )
    return render_template(
        "instructor/section_detail.html",
        section=section,
        enrollments=enrollments,
        assignments=assignments_list,
    )


# ---------------------------------------------------------------------------
# Instructor leaderboard view
# ---------------------------------------------------------------------------

@instructor_bp.route("/assignment/<int:assignment_id>/leaderboard")
@login_required
@instructor_required
def leaderboard(assignment_id: int):
    instr = _get_instructor_or_404()
    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.section.course.instructor_id != instr.id:
        abort(403)

    section = assignment.section
    enrollments = Enrollment.query.filter_by(section_id=section.id).all()

    board = []
    for enr in enrollments:
        user = enr.user
        best = _best_submission(user.id, assignment)
        all_subs = (
            Submission.query
            .filter_by(user_id=user.id, assignment_id=assignment_id)
            .count()
        )
        board.append(
            {
                "alias": user.display_name,
                "email": user.email,
                "score": best.score if best else None,
                "submitted_at": best.submitted_at if best else None,
                "total_submissions": all_subs,
            }
        )

    reverse = assignment.higher_is_better
    board.sort(
        key=lambda x: (
            x["score"] is None,
            (-x["score"] if x["score"] is not None and reverse else x["score"]) or 0,
        )
    )
    _assign_ranks(board, reverse)

    return render_template(
        "instructor/leaderboard.html",
        assignment=assignment,
        section=section,
        board=board,
    )


# ---------------------------------------------------------------------------
# Student enrollment management
# ---------------------------------------------------------------------------

@instructor_bp.route("/section/<int:section_id>/enroll", methods=["POST"])
@login_required
@instructor_required
def enroll_student(section_id: int):
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if section.course.instructor_id != instr.id:
        abort(403)

    email = request.form.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()
    if not user:
        flash(f"No user found with email '{email}'.", "danger")
    elif Enrollment.query.filter_by(user_id=user.id, section_id=section_id).first():
        flash(f"{email} is already enrolled.", "warning")
    else:
        db.session.add(Enrollment(user_id=user.id, section_id=section_id))
        db.session.commit()
        flash(f"{email} enrolled successfully.", "success")

    return redirect(url_for("instructor.section_detail", section_id=section_id))


@instructor_bp.route("/enrollment/<int:enrollment_id>/remove", methods=["POST"])
@login_required
@instructor_required
def remove_enrollment(enrollment_id: int):
    instr = _get_instructor_or_404()
    enrollment = Enrollment.query.get_or_404(enrollment_id)
    if enrollment.section.course.instructor_id != instr.id:
        abort(403)

    section_id = enrollment.section_id
    db.session.delete(enrollment)
    db.session.commit()
    flash("Student removed from section.", "info")
    return redirect(url_for("instructor.section_detail", section_id=section_id))


# ---------------------------------------------------------------------------
# Admin — User Management
# ---------------------------------------------------------------------------

@instructor_bp.route("/admin/users")
@login_required
@admin_required
def admin_users():
    semester_id = request.args.get("semester_id", type=int)
    course_id   = request.args.get("course_id",   type=int)
    section_id  = request.args.get("section_id",  type=int)
    search      = request.args.get("q", "").strip()

    query = (
        User.query
        .filter(User.is_admin == False)  # noqa: E712
        .order_by(User.created_at.desc())
    )

    if search:
        query = query.filter(User.email.ilike(f"%{search}%"))

    if section_id:
        query = (
            query.join(Enrollment, Enrollment.user_id == User.id)
                 .filter(Enrollment.section_id == section_id)
        )
    elif course_id:
        query = (
            query.join(Enrollment, Enrollment.user_id == User.id)
                 .join(Section, Section.id == Enrollment.section_id)
                 .filter(Section.course_id == course_id)
        )
    elif semester_id:
        query = (
            query.join(Enrollment, Enrollment.user_id == User.id)
                 .join(Section, Section.id == Enrollment.section_id)
                 .filter(Section.semester_id == semester_id)
        )

    users = query.all()
    semesters = Semester.query.order_by(Semester.name).all()
    courses   = Course.query.order_by(Course.name).all()
    sections  = Section.query.order_by(Section.section_name).all()

    return render_template(
        "instructor/admin_users.html",
        users=users,
        semesters=semesters,
        courses=courses,
        sections=sections,
        selected_semester=semester_id,
        selected_course=course_id,
        selected_section=section_id,
        search=search,
    )


@instructor_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id: int):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        abort(403)
    Enrollment.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f"User {user.email} deleted.", "success")
    return redirect(url_for("instructor.admin_users", **request.form))


@instructor_bp.route("/admin/users/<int:user_id>/verify", methods=["POST"])
@login_required
@admin_required
def admin_verify_user(user_id: int):
    user = User.query.get_or_404(user_id)
    user.is_verified = True
    db.session.commit()
    flash(f"{user.email} marked as verified.", "success")
    return redirect(url_for("instructor.admin_users", **request.form))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in {"csv"}
    )


def _best_submission(user_id: int, assignment: Assignment):
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

import io
import json
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

from models import (
    Assignment,
    AssignmentFile,
    Course,
    CourseAssignment,
    Enrollment,
    Instructor,
    METRIC_CHOICES,
    Section,
    SectionOverride,
    Semester,
    Submission,
    User,
    db,
)

from . import instructor_bp
from utils import storage


# ---------------------------------------------------------------------------
# Access guards
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


def _owns_assignment(instr: Instructor, assignment: Assignment) -> bool:
    """True if this instructor created the assignment (or is admin)."""
    if current_user.is_admin:
        return True
    return assignment.instructor_id == instr.id


def _owns_course(instr: Instructor, course: Course) -> bool:
    if current_user.is_admin:
        return True
    return course.instructor_id == instr.id


def _owns_section(instr: Instructor, section: Section) -> bool:
    return _owns_course(instr, section.course)


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
        if instr else 0
    )
    total_submissions = (
        Submission.query
        .join(Assignment)
        .join(CourseAssignment, CourseAssignment.assignment_id == Assignment.id)
        .join(Course, Course.id == CourseAssignment.course_id)
        .filter(Course.instructor_id == instr.id)
        .count()
        if instr else 0
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
            if not _owns_course(instr, course):
                abort(403)
            course.is_active = not course.is_active
            db.session.commit()
            flash(f"Course '{course.name}' updated.", "info")

        return redirect(url_for("instructor.courses"))

    all_courses = instr.courses.order_by(Course.name.asc()).all() if instr else []
    return render_template("instructor/courses.html", courses=all_courses, instructor=instr)


# ---------------------------------------------------------------------------
# Course detail (sections + linked assignments)
# ---------------------------------------------------------------------------

@instructor_bp.route("/course/<int:course_id>", methods=["GET", "POST"])
@login_required
@instructor_required
def course_detail(course_id: int):
    instr = _get_instructor_or_404()
    course = Course.query.get_or_404(course_id)
    if not _owns_course(instr, course):
        abort(403)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create_section":
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

        elif action == "toggle_section":
            section_id = request.form.get("section_id", type=int)
            section = Section.query.get_or_404(section_id)
            section.is_active = not section.is_active
            db.session.commit()
            flash("Section status updated.", "info")

        elif action == "add_assignment":
            assignment_id = request.form.get("assignment_id", type=int)
            if assignment_id:
                assignment = Assignment.query.get_or_404(assignment_id)
                existing = CourseAssignment.query.filter_by(
                    course_id=course.id, assignment_id=assignment_id
                ).first()
                if existing:
                    existing.is_active = True
                    flash(f"'{assignment.title}' re-enabled for this course.", "info")
                else:
                    db.session.add(CourseAssignment(
                        course_id=course.id, assignment_id=assignment_id
                    ))
                    flash(f"'{assignment.title}' added to this course.", "success")
                db.session.commit()

        elif action == "remove_assignment":
            assignment_id = request.form.get("assignment_id", type=int)
            ca = CourseAssignment.query.filter_by(
                course_id=course.id, assignment_id=assignment_id
            ).first_or_404()
            db.session.delete(ca)
            db.session.commit()
            flash("Assignment removed from this course.", "info")

        return redirect(url_for("instructor.course_detail", course_id=course_id))

    # Build the linked assignments list
    linked = (
        CourseAssignment.query
        .filter_by(course_id=course.id)
        .order_by(CourseAssignment.created_at.desc())
        .all()
    )

    # Build pool options (assignments by this instructor not already linked)
    linked_ids = {ca.assignment_id for ca in linked}
    if current_user.is_admin:
        pool_options = Assignment.query.filter(
            Assignment.is_active == True,
            ~Assignment.id.in_(linked_ids) if linked_ids else True
        ).order_by(Assignment.title).all()
    else:
        pool_options = (
            Assignment.query
            .filter(
                Assignment.instructor_id == instr.id,
                Assignment.is_active == True,
                ~Assignment.id.in_(linked_ids) if linked_ids else True,
            )
            .order_by(Assignment.title)
            .all()
        )

    all_sections = (
        course.sections
        .join(Semester)
        .order_by(Semester.name.desc(), Section.section_name.asc())
        .all()
    )
    semesters = Semester.query.filter_by(is_active=True).order_by(Semester.name.asc()).all()

    return render_template(
        "instructor/course_detail.html",
        course=course,
        sections=all_sections,
        semesters=semesters,
        linked=linked,
        pool_options=pool_options,
    )


# Backward-compat redirect for old sections URL
@instructor_bp.route("/courses/<int:course_id>/sections", methods=["GET", "POST"])
@login_required
@instructor_required
def sections(course_id: int):
    return redirect(url_for("instructor.course_detail", course_id=course_id), 301)


# ---------------------------------------------------------------------------
# Assignment pool
# ---------------------------------------------------------------------------

@instructor_bp.route("/assignments", methods=["GET"])
@login_required
@instructor_required
def assignments():
    instr = _get_instructor_or_404()
    if current_user.is_admin:
        pool = Assignment.query.order_by(Assignment.created_at.desc()).all()
    else:
        pool = (
            Assignment.query
            .filter_by(instructor_id=instr.id)
            .order_by(Assignment.created_at.desc())
            .all()
        )
    return render_template("instructor/assignment_pool.html", assignments=pool)


@instructor_bp.route("/assignments/new", methods=["GET", "POST"])
@login_required
@instructor_required
def new_assignment():
    instr = _get_instructor_or_404()

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
                    metric_choices=METRIC_CHOICES,
                )

        if not title:
            flash("Assignment title is required.", "danger")
            return render_template(
                "instructor/new_assignment.html",
                metric_choices=METRIC_CHOICES,
            )

        pm_config = None
        if metric == "profit_matrix":
            pm_config = json.dumps({
                "tp": request.form.get("pm_tp", type=float, default=1250),
                "fp": request.form.get("pm_fp", type=float, default=-250),
                "tn": request.form.get("pm_tn", type=float, default=0),
                "fn": request.form.get("pm_fn", type=float, default=0),
                "marketing_cost_per_positive": request.form.get("pm_mktg_cost", type=float, default=250),
                "constraint_name": request.form.get("pm_constraint_name", "Marketing Expenditure").strip(),
                "constraint_limit": request.form.get("pm_constraint_limit", type=float, default=150000),
            })

        assignment = Assignment(
            instructor_id=instr.id,
            title=title,
            description=description or None,
            scoring_metric=metric,
            target_column=target_col or None,
            max_submissions_per_day=max_subs,
            due_date=due_date,
            profit_matrix_config=pm_config,
        )
        db.session.add(assignment)
        db.session.flush()

        _save_assignment_files(assignment)

        gt_file = request.files.get("ground_truth_file")
        if gt_file and gt_file.filename:
            if _allowed_file(gt_file.filename):
                fname = f"assignment_{assignment.id}_{uuid.uuid4().hex}_gt.csv"
                storage.upload_fileobj(gt_file.stream, f"ground_truth/{fname}")
                assignment.ground_truth_filename = fname
            else:
                flash("Ground truth must be a CSV file.", "warning")

        db.session.commit()
        flash(f"Assignment '{title}' created and added to your pool.", "success")
        return redirect(url_for("instructor.assignments"))

    return render_template(
        "instructor/new_assignment.html",
        metric_choices=METRIC_CHOICES,
    )


@instructor_bp.route("/assignment/<int:assignment_id>/edit", methods=["GET", "POST"])
@login_required
@instructor_required
def edit_assignment(assignment_id: int):
    instr = _get_instructor_or_404()
    assignment = Assignment.query.get_or_404(assignment_id)
    if not _owns_assignment(instr, assignment):
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

        if assignment.scoring_metric == "profit_matrix":
            assignment.profit_matrix_config = json.dumps({
                "tp": request.form.get("pm_tp", type=float, default=1250),
                "fp": request.form.get("pm_fp", type=float, default=-250),
                "tn": request.form.get("pm_tn", type=float, default=0),
                "fn": request.form.get("pm_fn", type=float, default=0),
                "marketing_cost_per_positive": request.form.get("pm_mktg_cost", type=float, default=250),
                "constraint_name": request.form.get("pm_constraint_name", "Marketing Expenditure").strip(),
                "constraint_limit": request.form.get("pm_constraint_limit", type=float, default=150000),
            })
        else:
            assignment.profit_matrix_config = None

        # Delete files marked for removal
        delete_ids = request.form.getlist("delete_file_ids")
        if delete_ids:
            AssignmentFile.query.filter(
                AssignmentFile.id.in_([int(i) for i in delete_ids if i.isdigit()]),
                AssignmentFile.assignment_id == assignment.id,
            ).delete(synchronize_session=False)

        # Add new uploaded files
        _save_assignment_files(assignment)

        gt_file = request.files.get("ground_truth_file")
        if gt_file and gt_file.filename:
            if _allowed_file(gt_file.filename):
                fname = f"assignment_{assignment.id}_{uuid.uuid4().hex}_gt.csv"
                storage.upload_fileobj(gt_file.stream, f"ground_truth/{fname}")
                assignment.ground_truth_filename = fname

        db.session.commit()
        flash("Assignment updated.", "success")
        return redirect(url_for("instructor.assignments"))

    existing_files = assignment.files.all()
    return render_template(
        "instructor/edit_assignment.html",
        assignment=assignment,
        metric_choices=METRIC_CHOICES,
        existing_files=existing_files,
    )


# ---------------------------------------------------------------------------
# Section detail
# ---------------------------------------------------------------------------

@instructor_bp.route("/section/<int:section_id>")
@login_required
@instructor_required
def section_detail(section_id: int):
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if not _owns_section(instr, section):
        abort(403)

    enrollments = Enrollment.query.filter_by(section_id=section_id).all()
    effective = section.get_effective_assignments()

    # Build override state map: assignment_id → excluded bool
    overrides = {o.assignment_id: o.excluded for o in section.section_overrides.all()}

    # Pool assignments that are NOT already in the course and NOT in effective
    # (for section-level adds)
    course_aid_set = {
        ca.assignment_id
        for ca in section.course.course_assignments.filter_by(is_active=True).all()
    }
    added_ids = {aid for aid, excl in overrides.items() if not excl}
    effective_ids = {a.id for a in effective}

    if current_user.is_admin:
        section_add_options = Assignment.query.filter(
            Assignment.is_active == True,
            ~Assignment.id.in_(effective_ids) if effective_ids else True,
        ).order_by(Assignment.title).all()
    else:
        section_add_options = Assignment.query.filter(
            Assignment.instructor_id == instr.id,
            Assignment.is_active == True,
            ~Assignment.id.in_(effective_ids) if effective_ids else True,
        ).order_by(Assignment.title).all()

    return render_template(
        "instructor/section_detail.html",
        section=section,
        enrollments=enrollments,
        assignments=effective,
        overrides=overrides,
        course_aid_set=course_aid_set,
        section_add_options=section_add_options,
    )


# ---------------------------------------------------------------------------
# Section-level assignment overrides
# ---------------------------------------------------------------------------

@instructor_bp.route("/section/<int:section_id>/assignment/<int:assignment_id>/exclude", methods=["POST"])
@login_required
@instructor_required
def section_exclude_assignment(section_id: int, assignment_id: int):
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if not _owns_section(instr, section):
        abort(403)
    Assignment.query.get_or_404(assignment_id)

    existing = SectionOverride.query.filter_by(
        section_id=section_id, assignment_id=assignment_id
    ).first()
    if existing:
        existing.excluded = True
    else:
        db.session.add(SectionOverride(
            section_id=section_id, assignment_id=assignment_id, excluded=True
        ))
    db.session.commit()
    flash("Assignment hidden from this section.", "info")
    return redirect(url_for("instructor.section_detail", section_id=section_id))


@instructor_bp.route("/section/<int:section_id>/assignment/<int:assignment_id>/include", methods=["POST"])
@login_required
@instructor_required
def section_include_assignment(section_id: int, assignment_id: int):
    """Remove an exclude override (restores inherited), or add a section-only assignment."""
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if not _owns_section(instr, section):
        abort(403)
    Assignment.query.get_or_404(assignment_id)

    action = request.form.get("override_action", "restore")

    if action == "add":
        # Add to this section only (not course-wide)
        existing = SectionOverride.query.filter_by(
            section_id=section_id, assignment_id=assignment_id
        ).first()
        if existing:
            existing.excluded = False
        else:
            db.session.add(SectionOverride(
                section_id=section_id, assignment_id=assignment_id, excluded=False
            ))
        db.session.commit()
        flash("Assignment added to this section.", "success")
    else:
        # Remove exclude override (restore inheritance)
        override = SectionOverride.query.filter_by(
            section_id=section_id, assignment_id=assignment_id, excluded=True
        ).first()
        if override:
            db.session.delete(override)
            db.session.commit()
        flash("Assignment restored for this section.", "info")

    return redirect(url_for("instructor.section_detail", section_id=section_id))


# ---------------------------------------------------------------------------
# Leaderboard (section-scoped)
# ---------------------------------------------------------------------------

@instructor_bp.route("/section/<int:section_id>/assignment/<int:assignment_id>/leaderboard")
@login_required
@instructor_required
def leaderboard(section_id: int, assignment_id: int):
    instr = _get_instructor_or_404()
    section = Section.query.get_or_404(section_id)
    if not _owns_section(instr, section):
        abort(403)
    assignment = Assignment.query.get_or_404(assignment_id)

    enrollments = Enrollment.query.filter_by(section_id=section_id).all()

    board = []
    for enr in enrollments:
        user = enr.user
        best = _best_submission_for_section(user.id, assignment_id, section_id, assignment)
        all_subs = (
            Submission.query
            .filter_by(user_id=user.id, assignment_id=assignment_id)
            .count()
        )
        board.append({
            "alias": user.display_name,
            "email": user.email,
            "score": best.score if best else None,
            "submitted_at": best.submitted_at if best else None,
            "total_submissions": all_subs,
            "detail": best.detail if best else {},
        })

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
    if not _owns_section(instr, section):
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
    if not _owns_section(instr, enrollment.section):
        abort(403)

    section_id = enrollment.section_id
    db.session.delete(enrollment)
    db.session.commit()
    flash("Student removed from section.", "info")
    return redirect(url_for("instructor.section_detail", section_id=section_id))


# ---------------------------------------------------------------------------
# Instructor profile
# ---------------------------------------------------------------------------

@instructor_bp.route("/profile", methods=["GET", "POST"])
@login_required
@instructor_required
def profile():
    instr = _get_instructor_or_404()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        department = request.form.get("department", "").strip()

        if not name:
            flash("Name cannot be empty.", "danger")
        elif len(name) > 128:
            flash("Name must be 128 characters or fewer.", "danger")
        else:
            instr.name = name
            instr.department = department or None
            db.session.commit()
            flash("Profile updated.", "success")

        return redirect(url_for("instructor.profile"))

    return render_template("instructor/profile.html", instructor=instr)


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
    Submission.query.filter_by(user_id=user.id).delete()
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


def _save_assignment_files(assignment: Assignment) -> None:
    """Upload any new dataset files from the current request and create AssignmentFile rows."""
    uploaded_files = request.files.getlist("new_files")
    display_names = request.form.getlist("new_display_names")

    for i, f in enumerate(uploaded_files):
        if not f or not f.filename:
            continue
        if not _allowed_file(f.filename):
            flash(f"Skipped '{f.filename}': only CSV files are accepted.", "warning")
            continue
        name = (display_names[i].strip() if i < len(display_names) else "").strip()
        if not name:
            # Default display name: original filename without extension
            name = f.filename.rsplit(".", 1)[0] if "." in f.filename else f.filename
        r2_key = f"datasets/{assignment.id}/{uuid.uuid4().hex}_{f.filename}"
        storage.upload_fileobj(f.stream, r2_key)
        db.session.add(AssignmentFile(
            assignment_id=assignment.id,
            display_name=name,
            r2_key=r2_key,
        ))


def _best_submission_for_section(user_id: int, assignment_id: int, section_id: int, assignment: Assignment):
    """Best submission scoped to a section. Falls back to section_id=NULL for old rows."""
    subs = (
        Submission.query
        .filter_by(user_id=user_id, assignment_id=assignment_id, section_id=section_id)
        .filter(Submission.score.isnot(None))
        .all()
    )
    if not subs:
        subs = (
            Submission.query
            .filter_by(user_id=user_id, assignment_id=assignment_id)
            .filter(Submission.score.isnot(None), Submission.section_id.is_(None))
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

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    alias = db.Column(db.String(64), unique=True, nullable=True)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    is_instructor = db.Column(db.Boolean, default=False, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    enrollments = db.relationship("Enrollment", back_populates="user", lazy="dynamic")
    submissions = db.relationship("Submission", back_populates="user", lazy="dynamic")
    instructor_profile = db.relationship(
        "Instructor", back_populates="user", uselist=False
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self) -> str:
        return self.alias or self.email.split("@")[0]

    def __repr__(self) -> str:
        return f"<User {self.email}>"


# ---------------------------------------------------------------------------
# Semester
# ---------------------------------------------------------------------------

class Semester(db.Model):
    __tablename__ = "semesters"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    sections = db.relationship("Section", back_populates="semester", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Semester {self.name}>"


# ---------------------------------------------------------------------------
# Instructor
# ---------------------------------------------------------------------------

class Instructor(db.Model):
    __tablename__ = "instructors"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    name = db.Column(db.String(128), nullable=False)
    department = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", back_populates="instructor_profile")
    courses = db.relationship("Course", back_populates="instructor", lazy="dynamic")
    # Assignments created by this instructor (the pool)
    pool_assignments = db.relationship("Assignment", back_populates="instructor", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Instructor {self.name}>"


# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------

class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    instructor_id = db.Column(
        db.Integer, db.ForeignKey("instructors.id"), nullable=False
    )
    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(32), nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    instructor = db.relationship("Instructor", back_populates="courses")
    sections = db.relationship("Section", back_populates="course", lazy="dynamic")
    course_assignments = db.relationship(
        "CourseAssignment", back_populates="course", lazy="dynamic",
        cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        if self.code:
            return f"{self.code} – {self.name}"
        return self.name

    def __repr__(self) -> str:
        return f"<Course {self.code} {self.name}>"


# ---------------------------------------------------------------------------
# Section
# ---------------------------------------------------------------------------

class Section(db.Model):
    __tablename__ = "sections"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey("semesters.id"), nullable=False)
    section_name = db.Column(db.String(64), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    course = db.relationship("Course", back_populates="sections")
    semester = db.relationship("Semester", back_populates="sections")
    enrollments = db.relationship("Enrollment", back_populates="section", lazy="dynamic")
    section_overrides = db.relationship(
        "SectionOverride", back_populates="section", lazy="dynamic",
        cascade="all, delete-orphan"
    )
    submissions = db.relationship("Submission", back_populates="section", lazy="dynamic")

    @property
    def full_label(self) -> str:
        course = self.course
        instructor = course.instructor
        return (
            f"{self.semester.name} | {instructor.name} | "
            f"{course.display_name} | Section {self.section_name}"
        )

    def get_effective_assignments(self):
        """Return assignments visible to this section (course pool + overrides)."""
        course_aid_set = {
            ca.assignment_id
            for ca in self.course.course_assignments.filter_by(is_active=True).all()
        }
        overrides = self.section_overrides.all()
        excluded = {o.assignment_id for o in overrides if o.excluded}
        added    = {o.assignment_id for o in overrides if not o.excluded}
        effective = (course_aid_set - excluded) | added
        if not effective:
            return []
        assignments = (
            Assignment.query
            .filter(Assignment.id.in_(effective), Assignment.is_active == True)
            .all()
        )
        for a in assignments:
            a.section_due_date = self.effective_due_date(a)
        assignments.sort(key=lambda a: (a.section_due_date is None, a.section_due_date))
        return assignments

    def effective_due_date(self, assignment):
        """Return the due date this section should use for `assignment`,
        accounting for a per-section override (including an explicit
        "no due date" override)."""
        override = SectionOverride.query.filter_by(
            section_id=self.id, assignment_id=assignment.id
        ).first()
        if override is not None and override.has_due_date_override:
            return override.due_date_override
        return assignment.due_date

    def __repr__(self) -> str:
        return f"<Section {self.section_name} course={self.course_id}>"


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class Enrollment(db.Model):
    __tablename__ = "enrollments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=False)
    enrolled_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("user_id", "section_id", name="uq_enrollment"),
    )

    user = db.relationship("User", back_populates="enrollments")
    section = db.relationship("Section", back_populates="enrollments")

    def __repr__(self) -> str:
        return f"<Enrollment user={self.user_id} section={self.section_id}>"


# ---------------------------------------------------------------------------
# Assignment (pool — not tied to any specific section or course)
# ---------------------------------------------------------------------------

SCORING_METRICS = [
    ("rmse", "RMSE (lower is better)"),
    ("mae", "MAE (lower is better)"),
    ("accuracy", "Accuracy (higher is better)"),
    ("f1", "F1 Score (higher is better)"),
    ("auc", "AUC-ROC (higher is better)"),
    ("r2", "R² Score (higher is better)"),
    ("profit_matrix", "Profit Matrix (higher is better)"),
]

METRIC_CHOICES = [(m[0], m[1]) for m in SCORING_METRICS]

# Sentinel for Assignment.is_past_due() so "no argument passed" (use self.due_date)
# and "override explicitly says None" (no due date) are distinguishable.
_UNSET = object()


class Assignment(db.Model):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    # instructor_id is nullable to support legacy rows that had section_id instead
    instructor_id = db.Column(db.Integer, db.ForeignKey("instructors.id"), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    dataset_filename = db.Column(db.String(255), nullable=True)
    ground_truth_filename = db.Column(db.String(255), nullable=True)
    scoring_metric = db.Column(db.String(32), nullable=False, default="rmse")
    target_column = db.Column(db.String(128), nullable=True)
    max_submissions_per_day = db.Column(db.Integer, default=3, nullable=False)
    due_date = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    profit_matrix_config = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    instructor = db.relationship("Instructor", back_populates="pool_assignments")
    course_assignments = db.relationship(
        "CourseAssignment", back_populates="assignment", lazy="dynamic",
        cascade="all, delete-orphan"
    )
    section_overrides = db.relationship(
        "SectionOverride", back_populates="assignment", lazy="dynamic",
        cascade="all, delete-orphan"
    )
    submissions = db.relationship("Submission", back_populates="assignment", lazy="dynamic")
    files = db.relationship(
        "AssignmentFile", back_populates="assignment", lazy="dynamic",
        cascade="all, delete-orphan", order_by="AssignmentFile.created_at"
    )

    @property
    def higher_is_better(self) -> bool:
        return self.scoring_metric in ("accuracy", "f1", "auc", "r2", "profit_matrix")

    @property
    def metric_label(self) -> str:
        for key, label in SCORING_METRICS:
            if key == self.scoring_metric:
                return label
        return self.scoring_metric.upper()

    @property
    def profit_matrix_cfg(self) -> dict:
        import json
        if self.profit_matrix_config:
            try:
                return json.loads(self.profit_matrix_config)
            except (ValueError, TypeError):
                pass
        return {
            "tp": 1250, "fp": -250, "tn": 0, "fn": 0,
            "marketing_cost_per_positive": 250,
            "constraint_name": "Marketing Expenditure",
            "constraint_limit": 150000,
        }

    def is_past_due(self, effective_due_date=_UNSET) -> bool:
        dd = self.due_date if effective_due_date is _UNSET else effective_due_date
        if dd is None:
            return False
        return datetime.now(timezone.utc) > dd.replace(tzinfo=timezone.utc)

    def linked_course_count(self) -> int:
        return self.course_assignments.filter_by(is_active=True).count()

    def __repr__(self) -> str:
        return f"<Assignment {self.title}>"


# ---------------------------------------------------------------------------
# AssignmentFile — one or more downloadable files attached to an assignment
# ---------------------------------------------------------------------------

class AssignmentFile(db.Model):
    __tablename__ = "assignment_files"

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    display_name = db.Column(db.String(255), nullable=False)
    r2_key = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    assignment = db.relationship("Assignment", back_populates="files")

    def __repr__(self) -> str:
        return f"<AssignmentFile {self.display_name} assignment={self.assignment_id}>"


# ---------------------------------------------------------------------------
# CourseAssignment — links an assignment from the pool to a course
# ---------------------------------------------------------------------------

class CourseAssignment(db.Model):
    __tablename__ = "course_assignments"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("course_id", "assignment_id", name="uq_course_assignment"),
    )

    course = db.relationship("Course", back_populates="course_assignments")
    assignment = db.relationship("Assignment", back_populates="course_assignments")

    def __repr__(self) -> str:
        return f"<CourseAssignment course={self.course_id} assignment={self.assignment_id}>"


# ---------------------------------------------------------------------------
# SectionOverride — per-section add or exclude of an assignment
# ---------------------------------------------------------------------------

class SectionOverride(db.Model):
    __tablename__ = "section_overrides"

    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    # excluded=True  → hide an inherited assignment from this section
    # excluded=False → add an assignment to this section only (not course-wide)
    excluded = db.Column(db.Boolean, default=False, nullable=False)
    # Per-section due date override.
    # has_due_date_override=False -> section inherits Assignment.due_date
    # has_due_date_override=True, due_date_override=<datetime> -> section uses that date
    # has_due_date_override=True, due_date_override=None -> section explicitly has no due date
    due_date_override = db.Column(db.DateTime, nullable=True)
    has_due_date_override = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("section_id", "assignment_id", name="uq_section_override"),
    )

    section = db.relationship("Section", back_populates="section_overrides")
    assignment = db.relationship("Assignment", back_populates="section_overrides")

    def __repr__(self) -> str:
        action = "exclude" if self.excluded else "add"
        return f"<SectionOverride section={self.section_id} assignment={self.assignment_id} {action}>"


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    # section_id added post-launch; nullable for backward compat with old rows
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=True)
    filename = db.Column(db.String(255), nullable=False)
    score = db.Column(db.Float, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    score_detail = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", back_populates="submissions")
    assignment = db.relationship("Assignment", back_populates="submissions")
    section = db.relationship("Section", back_populates="submissions")

    @property
    def detail(self) -> dict:
        import json
        if self.score_detail:
            try:
                return json.loads(self.score_detail)
            except (ValueError, TypeError):
                pass
        return {}

    def __repr__(self) -> str:
        return f"<Submission user={self.user_id} assignment={self.assignment_id} score={self.score}>"

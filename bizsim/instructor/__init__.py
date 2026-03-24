from flask import Blueprint

instructor_bp = Blueprint("instructor", __name__, url_prefix="/instructor")

from . import routes  # noqa: E402, F401

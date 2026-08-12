"""
Outlier-based auto-grading — converts raw assignment scores into course grades
using median/MAD-based modified z-scores.

See the design spec (outlier_grading_algorithm.md) for the exact math this
implements.
"""
import math
import statistics


def compute_grades(students, higher_is_better, fence, k,
                    grade_range_lower, grade_range_upper, absolute_low_score):
    """
    students: list of {"user_id": int, "raw_score": float|None}

    Returns a dict keyed by user_id:
        {"z_score": float|None, "bucket": str, "computed_score": float|None}
    """
    result = {}

    scored = [s for s in students if s["raw_score"] is not None]
    unscored = [s for s in students if s["raw_score"] is None]

    for s in unscored:
        result[s["user_id"]] = {
            "z_score": None,
            "bucket": "no_submission",
            "computed_score": None,
        }

    if not scored:
        return result

    values = [s["raw_score"] for s in scored]
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])

    midpoint = (grade_range_upper + grade_range_lower) / 2

    if mad == 0:
        if all(v == values[0] for v in values):
            # All raw values identical -> everyone is ordinary, right at the midpoint.
            for s in scored:
                result[s["user_id"]] = {
                    "z_score": 0.0,
                    "bucket": "ordinary",
                    "computed_score": midpoint,
                }
            return result

        # MAD is zero but values differ (e.g. a tight cluster plus a lone
        # deviator) -> substitute a small epsilon so deviating students still
        # resolve to a large-but-finite z instead of a divide-by-zero.
        sorted_vals = sorted(set(values))
        diffs = [
            b - a for a, b in zip(sorted_vals, sorted_vals[1:])
            if (b - a) != 0
        ]
        mad = min(diffs) * 0.01 if diffs else 1e-6

    for s in scored:
        value = s["raw_score"]
        if higher_is_better:
            z = 0.6745 * (value - median) / mad
        else:
            z = 0.6745 * (median - value) / mad

        if -fence <= z <= fence:
            bucket = "ordinary"
            computed_score = z * (grade_range_upper - grade_range_lower) / (2 * fence) + midpoint
        elif z < -fence:
            bucket = "low_outlier"
            severity = -fence - z
            computed_score = (
                absolute_low_score
                + (grade_range_lower - absolute_low_score) * math.exp(-k * severity)
            )
        else:
            bucket = "high_outlier"
            computed_score = 0.0

        result[s["user_id"]] = {
            "z_score": z,
            "bucket": bucket,
            "computed_score": computed_score,
        }

    return result

"""
Scoring engine — compares a student's CSV submission against the ground truth.

Ground truth CSV must contain the target column.
Student CSV must contain a column matching assignment.target_column
(or a single non-id column that will be used automatically).
"""
from __future__ import annotations

import io
import os
from typing import Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)


def _load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def _coerce_predictions(
    gt: pd.DataFrame,
    sub: pd.DataFrame,
    target_col: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Align submission predictions to ground truth row order.

    Strategy:
      1. If both have an 'id' column, merge on it.
      2. Otherwise assume row-aligned and use positional order.
    Returns (y_true, y_pred) numpy arrays.
    """
    # Determine prediction column in submission
    if target_col and target_col in sub.columns:
        pred_col = target_col
    else:
        # Pick the first non-id column
        non_id = [c for c in sub.columns if c.lower() != "id"]
        if not non_id:
            raise ValueError("Submission CSV has no usable prediction column.")
        pred_col = non_id[0]

    if "id" in gt.columns and "id" in sub.columns:
        # Rename the prediction column before merging to avoid pandas adding
        # _x/_y suffixes when target_col and pred_col share the same name.
        sub_aligned = sub[["id", pred_col]].rename(columns={pred_col: "__pred__"})
        merged = gt[["id", target_col]].merge(sub_aligned, on="id", how="left")
        y_true = merged[target_col].values
        y_pred = merged["__pred__"].values
    else:
        if len(gt) != len(sub):
            raise ValueError(
                f"Row count mismatch: ground truth has {len(gt)} rows, "
                f"submission has {len(sub)} rows."
            )
        y_true = gt[target_col].values
        y_pred = sub[pred_col].values

    if np.isnan(y_pred.astype(float)).any():
        raise ValueError("Submission contains NaN values in the prediction column.")

    return y_true.astype(float), y_pred.astype(float)


def score_submission(
    submission_path: str,
    ground_truth_path: str,
    metric: str,
    target_col: str,
) -> float:
    """
    Score a student submission CSV against the ground truth.

    Parameters
    ----------
    submission_path : str
        Path to the student's uploaded CSV.
    ground_truth_path : str
        Path to the instructor's ground truth CSV.
    metric : str
        One of: rmse, mae, accuracy, f1, auc, r2
    target_col : str
        Column name containing the target values in the ground truth CSV.

    Returns
    -------
    float
        The computed score.

    Raises
    ------
    ValueError
        If the files can't be aligned or the metric is unknown.
    """
    gt = _load_csv(ground_truth_path)
    sub = _load_csv(submission_path)

    if target_col not in gt.columns:
        raise ValueError(
            f"Target column '{target_col}' not found in ground truth CSV. "
            f"Available columns: {list(gt.columns)}"
        )

    y_true, y_pred = _coerce_predictions(gt, sub, target_col)

    metric = metric.lower()

    if metric == "rmse":
        return float(np.sqrt(mean_squared_error(y_true, y_pred)))

    elif metric == "mae":
        return float(mean_absolute_error(y_true, y_pred))

    elif metric == "accuracy":
        y_true_int = y_true.round().astype(int)
        y_pred_int = y_pred.round().astype(int)
        return float(accuracy_score(y_true_int, y_pred_int))

    elif metric == "f1":
        y_true_int = y_true.round().astype(int)
        y_pred_int = y_pred.round().astype(int)
        avg = "binary" if len(np.unique(y_true_int)) <= 2 else "macro"
        return float(f1_score(y_true_int, y_pred_int, average=avg, zero_division=0))

    elif metric == "auc":
        return float(roc_auc_score(y_true, y_pred))

    elif metric == "r2":
        return float(r2_score(y_true, y_pred))

    else:
        raise ValueError(f"Unknown scoring metric: '{metric}'")


def score_from_streams(
    submission_bytes: bytes,
    ground_truth_bytes: bytes,
    metric: str,
    target_col: str,
) -> float:
    """Score using both submission and ground truth supplied as bytes."""
    gt = pd.read_csv(io.BytesIO(ground_truth_bytes))
    sub = pd.read_csv(io.BytesIO(submission_bytes))

    if target_col not in gt.columns:
        raise ValueError(
            f"Target column '{target_col}' not found in ground truth CSV."
        )

    y_true, y_pred = _coerce_predictions(gt, sub, target_col)

    metric = metric.lower()
    if metric == "rmse":
        return float(np.sqrt(mean_squared_error(y_true, y_pred)))
    elif metric == "mae":
        return float(mean_absolute_error(y_true, y_pred))
    elif metric == "accuracy":
        return float(accuracy_score(y_true.round().astype(int), y_pred.round().astype(int)))
    elif metric == "f1":
        y_ti = y_true.round().astype(int)
        y_pi = y_pred.round().astype(int)
        avg = "binary" if len(np.unique(y_ti)) <= 2 else "macro"
        return float(f1_score(y_ti, y_pi, average=avg, zero_division=0))
    elif metric == "auc":
        return float(roc_auc_score(y_true, y_pred))
    elif metric == "r2":
        return float(r2_score(y_true, y_pred))
    else:
        raise ValueError(f"Unknown scoring metric: '{metric}'")


def score_from_bytes(
    submission_bytes: bytes,
    ground_truth_path: str,
    metric: str,
    target_col: str,
) -> float:
    """Score from in-memory bytes (before saving to disk)."""
    gt = _load_csv(ground_truth_path)
    sub = pd.read_csv(io.BytesIO(submission_bytes))

    if target_col not in gt.columns:
        raise ValueError(
            f"Target column '{target_col}' not found in ground truth CSV."
        )

    y_true, y_pred = _coerce_predictions(gt, sub, target_col)

    # Reuse logic by writing to temp and calling score_submission would duplicate;
    # call inline instead.
    metric = metric.lower()
    if metric == "rmse":
        return float(np.sqrt(mean_squared_error(y_true, y_pred)))
    elif metric == "mae":
        return float(mean_absolute_error(y_true, y_pred))
    elif metric == "accuracy":
        return float(accuracy_score(y_true.round().astype(int), y_pred.round().astype(int)))
    elif metric == "f1":
        y_ti = y_true.round().astype(int)
        y_pi = y_pred.round().astype(int)
        avg = "binary" if len(np.unique(y_ti)) <= 2 else "macro"
        return float(f1_score(y_ti, y_pi, average=avg, zero_division=0))
    elif metric == "auc":
        return float(roc_auc_score(y_true, y_pred))
    elif metric == "r2":
        return float(r2_score(y_true, y_pred))
    else:
        raise ValueError(f"Unknown scoring metric: '{metric}'")

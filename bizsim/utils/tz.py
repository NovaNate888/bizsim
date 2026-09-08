from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/New_York")


def local_input_to_utc_naive(naive_local_dt: datetime) -> datetime:
    """Interpret a naive datetime as LOCAL_TZ wall-clock time; return naive UTC for storage."""
    aware_local = naive_local_dt.replace(tzinfo=LOCAL_TZ)
    return aware_local.astimezone(timezone.utc).replace(tzinfo=None)


def utc_naive_to_local(naive_utc_dt: datetime | None) -> datetime | None:
    """Interpret a naive datetime as UTC; return it converted to LOCAL_TZ (tz-aware)."""
    if naive_utc_dt is None:
        return None
    return naive_utc_dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)


def local_midnight_today_utc_naive() -> datetime:
    """Today's midnight in LOCAL_TZ, converted to naive UTC, for day-boundary comparisons."""
    now_local = datetime.now(LOCAL_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)

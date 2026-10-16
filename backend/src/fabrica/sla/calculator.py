from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fabrica.db.models import now


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=now().tzinfo)


def calculate_business_hours(
    start: datetime,
    end: datetime | None = None,
    *,
    working_days: tuple[int, ...] = (0, 1, 2, 3, 4),
    work_start_hour: int = 9,
    work_end_hour: int = 18,
    holidays: set[Any] | None = None,
) -> float:
    finish = end or now()
    if finish <= start:
        return 0.0

    st = _aware(start)
    fin = _aware(finish)
    holidays_set = holidays or set()

    current_date = st.date()
    end_date = fin.date()
    total_seconds = 0.0

    one_day = timedelta(days=1)
    while current_date <= end_date:
        if current_date.weekday() in working_days and current_date not in holidays_set:
            day_start = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                work_start_hour,
                0,
                0,
                tzinfo=st.tzinfo,
            )
            day_end = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                work_end_hour,
                0,
                0,
                tzinfo=st.tzinfo,
            )
            seg_start = max(st, day_start)
            seg_end = min(fin, day_end)
            if seg_end > seg_start:
                total_seconds += (seg_end - seg_start).total_seconds()
        current_date += one_day

    return round(total_seconds / 3600.0, 2)

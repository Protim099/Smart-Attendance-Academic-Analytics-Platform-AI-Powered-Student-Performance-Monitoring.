from django.conf import settings
from django.db.models import Count, Q


def summarize(qs):
    """Aggregate attendance records. Late counts as attended; excused is excluded from the denominator."""
    c = qs.aggregate(
        total=Count("id"),
        present=Count("id", filter=Q(status="present")),
        absent=Count("id", filter=Q(status="absent")),
        late=Count("id", filter=Q(status="late")),
        excused=Count("id", filter=Q(status="excused")),
    )
    return finalize(c)


def finalize(c):
    counted = c["total"] - c["excused"]
    attended = c["present"] + c["late"]
    c["percentage"] = round(attended / counted * 100, 1) if counted else None
    c["low"] = c["percentage"] is not None and c["percentage"] < settings.LOW_ATTENDANCE_THRESHOLD
    return c


AGG = dict(
    total=Count("id"),
    present=Count("id", filter=Q(status="present")),
    absent=Count("id", filter=Q(status="absent")),
    late=Count("id", filter=Q(status="late")),
    excused=Count("id", filter=Q(status="excused")),
)

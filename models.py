from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class AttendanceSession(TimeStampedModel):
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="sessions")
    schedule = models.ForeignKey("courses.ClassSchedule", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="sessions")
    date = models.DateField(db_index=True)
    topic = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey("teachers.Teacher", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="sessions")
    is_locked = models.BooleanField(default=False)
    qr_token = models.CharField(max_length=64, null=True, blank=True, unique=True)
    qr_created_at = models.DateTimeField(null=True, blank=True)
    qr_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["course", "date", "schedule"], name="uniq_session")]
        indexes = [models.Index(fields=["course", "date"])]

    def __str__(self):
        return f"{self.course.code} {self.date}"


class Attendance(TimeStampedModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LATE = "late", "Late"
        EXCUSED = "excused", "Excused"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        QR = "qr", "QR code"

    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="records")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="attendance_records")
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    remarks = models.CharField(max_length=200, blank=True)
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        ordering = ["-session__date", "student__student_id"]
        constraints = [models.UniqueConstraint(fields=["session", "student"], name="uniq_attendance")]
        indexes = [models.Index(fields=["student", "status"])]

    def __str__(self):
        return f"{self.student.student_id} {self.session} {self.status}"


class CorrectionRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="corrections")
    requested_status = models.CharField(max_length=10, choices=Attendance.Status.choices)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    review_note = models.CharField(max_length=250, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

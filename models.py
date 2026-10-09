from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Teacher(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile")
    employee_id = models.CharField(max_length=20, unique=True)
    department = models.ForeignKey("courses.Department", on_delete=models.PROTECT, related_name="teachers")
    designation = models.CharField(max_length=80, default="Lecturer")
    office_room = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["employee_id"]

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.username}"

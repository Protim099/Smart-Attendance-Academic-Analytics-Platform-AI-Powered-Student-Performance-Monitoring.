from django.db import models

from apps.common.models import TimeStampedModel


class Department(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Program(TimeStampedModel):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="programs")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=15, unique=True)
    duration_semesters = models.PositiveSmallIntegerField(default=8)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["department", "name"], name="uniq_program_per_dept")]

    def __str__(self):
        return self.name


class Semester(TimeStampedModel):
    class Term(models.TextChoices):
        SPRING = "spring", "Spring"
        SUMMER = "summer", "Summer"
        FALL = "fall", "Fall"

    name = models.CharField(max_length=50, unique=True)
    year = models.PositiveSmallIntegerField()
    term = models.CharField(max_length=10, choices=Term.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(check=models.Q(end_date__gt=models.F("start_date")), name="semester_dates_valid")
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            Semester.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)

    def __str__(self):
        return self.name


class Course(TimeStampedModel):
    code = models.CharField(max_length=15, unique=True)
    title = models.CharField(max_length=200)
    credit_hours = models.DecimalField(max_digits=3, decimal_places=1, default=3.0)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="courses")
    program = models.ForeignKey(Program, null=True, blank=True, on_delete=models.SET_NULL, related_name="courses")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="courses")
    teacher = models.ForeignKey("teachers.Teacher", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="courses")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        indexes = [models.Index(fields=["semester", "department"])]

    def __str__(self):
        return f"{self.code} - {self.title}"


class ClassSchedule(TimeStampedModel):
    class Day(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="schedules")
    day_of_week = models.PositiveSmallIntegerField(choices=Day.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["day_of_week", "start_time"]
        constraints = [
            models.CheckConstraint(check=models.Q(end_time__gt=models.F("start_time")), name="schedule_times_valid"),
            models.UniqueConstraint(fields=["course", "day_of_week", "start_time"], name="uniq_course_slot"),
        ]

    def __str__(self):
        return f"{self.course.code} {self.get_day_of_week_display()} {self.start_time:%H:%M}"

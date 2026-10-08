from django.db import models

from apps.common.models import TimeStampedModel
from apps.common.validators import validate_document

GRADE_SCALE = [  # (min percentage, letter, grade point) — UGC Bangladesh 4.0 scale
    (80, "A+", 4.00), (75, "A", 3.75), (70, "A-", 3.50), (65, "B+", 3.25), (60, "B", 3.00),
    (55, "B-", 2.75), (50, "C+", 2.50), (45, "C", 2.25), (40, "D", 2.00), (0, "F", 0.00),
]


def grade_for(percentage):
    for lo, letter, gp in GRADE_SCALE:
        if percentage >= lo:
            return letter, gp
    return "F", 0.0


class Assignment(TimeStampedModel):
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_date = models.DateTimeField()
    max_marks = models.DecimalField(max_digits=6, decimal_places=2, default=10)
    attachment = models.FileField(upload_to="assignments/", null=True, blank=True, validators=[validate_document])
    created_by = models.ForeignKey("teachers.Teacher", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="assignments")

    class Meta:
        ordering = ["-due_date"]
        indexes = [models.Index(fields=["course", "due_date"])]

    def __str__(self):
        return f"{self.course.code}: {self.title}"


class Submission(TimeStampedModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="submissions")
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="submissions/", null=True, blank=True, validators=[validate_document])
    marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [models.UniqueConstraint(fields=["assignment", "student"], name="uniq_submission")]


class Exam(TimeStampedModel):
    class Type(models.TextChoices):
        QUIZ = "quiz", "Quiz"
        MIDTERM = "midterm", "Midterm"
        FINAL = "final", "Final"
        LAB = "lab", "Lab"

    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="exams")
    title = models.CharField(max_length=150)
    exam_type = models.CharField(max_length=10, choices=Type.choices, default=Type.QUIZ)
    date = models.DateField()
    total_marks = models.DecimalField(max_digits=6, decimal_places=2, default=100)

    class Meta:
        ordering = ["-date"]
        indexes = [models.Index(fields=["course", "date"])]

    def __str__(self):
        return f"{self.course.code} {self.title}"


class Result(TimeStampedModel):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="results")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="results")
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    grade = models.CharField(max_length=3, blank=True)
    grade_point = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-exam__date", "student__student_id"]
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="uniq_result")]
        indexes = [models.Index(fields=["student", "exam"])]

    def save(self, *args, **kwargs):
        pct = float(self.marks_obtained) / float(self.exam.total_marks) * 100 if self.exam.total_marks else 0
        self.grade, gp = grade_for(pct)
        self.grade_point = gp
        super().save(*args, **kwargs)


class AcademicRecord(TimeStampedModel):
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="academic_records")
    semester = models.ForeignKey("courses.Semester", on_delete=models.CASCADE, related_name="academic_records")
    gpa = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    cgpa = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    credits = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        ordering = ["student__student_id", "-semester__start_date"]
        constraints = [models.UniqueConstraint(fields=["student", "semester"], name="uniq_academic_record")]

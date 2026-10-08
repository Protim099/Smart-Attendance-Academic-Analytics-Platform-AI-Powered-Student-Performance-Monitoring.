from django.db import transaction
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.accounts.audit import log_action
from apps.common.mixins import AuditMixin
from apps.common.permissions import (ADMIN_ROLES, DEPT_ADMIN, STUDENT, SUPER_ADMIN, TEACHER, IsStaff,
                                     IsStaffOrReadOnly)
from apps.common.scope import scoped_courses, scoped_students
from apps.notifications.services import notify, notify_many
from apps.students.models import Enrollment, Student

from .models import AcademicRecord, Assignment, Exam, Result, Submission
from .serializers import (AcademicRecordSerializer, AssignmentSerializer, ExamSerializer, ResultSerializer,
                          SubmissionSerializer)
from .services import recalculate_academic_record

PCT = ExpressionWrapper(Cast("results__marks_obtained", FloatField()) * 100.0 / Cast("total_marks", FloatField()),
                        output_field=FloatField())


class AssignmentViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = AssignmentSerializer
    permission_classes = [IsStaffOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    search_fields = ["title", "course__code", "course__title"]
    filterset_fields = ["course", "course__semester"]
    ordering_fields = ["due_date", "created_at", "title"]

    def get_queryset(self):
        return (Assignment.objects.select_related("course")
                .filter(course__in=scoped_courses(self.request.user))
                .annotate(submissions_count=Count("submissions", distinct=True)).order_by("-due_date"))

    def perform_create(self, serializer):
        teacher = getattr(self.request.user, "teacher_profile", None)
        obj = serializer.save(created_by=teacher or serializer.validated_data["course"].teacher)
        log_action(self.request, "CREATE", obj)
        students = [e.student.user for e in Enrollment.objects.filter(course=obj.course).exclude(status="dropped")
                    .select_related("student__user")]
        notify_many(students, "New assignment", f"{obj.course.code}: {obj.title} (due {obj.due_date:%d %b %Y}).",
                    "info", "/assignments")


class SubmissionViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = SubmissionSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filterset_fields = ["assignment", "student", "assignment__course"]
    search_fields = ["student__student_id", "student__user__first_name", "assignment__title"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        u = self.request.user
        qs = Submission.objects.select_related("assignment__course", "student__user")
        if u.role == SUPER_ADMIN:
            return qs
        if u.role == DEPT_ADMIN:
            return qs.filter(assignment__course__department=u.department)
        if u.role == TEACHER:
            return qs.filter(assignment__course__teacher__user=u)
        return qs.filter(student__user=u)

    def create(self, request, *args, **kwargs):
        if request.user.role != STUDENT:
            raise PermissionDenied("Only students can submit assignments.")
        student = request.user.student_profile
        existing = Submission.objects.filter(assignment_id=request.data.get("assignment"), student=student).first()
        if existing and existing.marks is not None:
            raise ValidationError("This submission has already been graded and can no longer be changed.")
        s = self.get_serializer(existing, data=request.data, partial=bool(existing))
        s.is_valid(raise_exception=True)
        obj = s.save(student=student)
        log_action(request, "SUBMIT", obj)
        return Response(s.data, status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        if self.request.user.role == STUDENT:
            raise PermissionDenied("Students cannot edit grades.")
        obj = serializer.save()
        log_action(self.request, "GRADE", obj, {"marks": str(obj.marks)})
        if obj.marks is not None:
            notify(obj.student.user, "Assignment graded",
                   f"{obj.assignment.title}: {obj.marks}/{obj.assignment.max_marks}", "success", "/assignments")


class ExamViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = ExamSerializer
    permission_classes = [IsStaffOrReadOnly]
    search_fields = ["title", "course__code"]
    filterset_fields = ["course", "exam_type", "course__semester"]
    ordering_fields = ["date", "title"]

    def get_queryset(self):
        return (Exam.objects.select_related("course").filter(course__in=scoped_courses(self.request.user))
                .annotate(average_percentage=Avg(PCT)).order_by("-date", "id"))


class ResultViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = ResultSerializer
    permission_classes = [IsStaffOrReadOnly]
    search_fields = ["student__student_id", "student__user__first_name", "student__user__last_name", "exam__title",
                     "exam__course__code"]
    filterset_fields = ["exam", "student", "exam__course", "exam__course__semester", "grade"]
    ordering_fields = ["marks_obtained", "exam__date", "student__student_id"]

    def get_queryset(self):
        u = self.request.user
        qs = Result.objects.select_related("exam__course", "student__user")
        if u.role == SUPER_ADMIN:
            return qs
        if u.role == DEPT_ADMIN:
            return qs.filter(exam__course__department=u.department)
        if u.role == TEACHER:
            return qs.filter(exam__course__teacher__user=u)
        return qs.filter(student__user=u)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        r = serializer.instance
        notify(r.student.user, "Result published", f"{r.exam.course.code} {r.exam.title}: {r.marks_obtained}/"
               f"{r.exam.total_marks} ({r.grade})", "info", "/results")

    @action(detail=False, methods=["post"], permission_classes=[IsStaff])
    def bulk(self, request):
        """POST {exam: id, records: [{student, marks_obtained}]} — upsert results for an exam."""
        try:
            exam = Exam.objects.select_related("course").get(pk=request.data.get("exam"))
        except (Exam.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"exam": "Valid exam is required."})
        u = request.user
        if (u.role == TEACHER and (not exam.course.teacher or exam.course.teacher.user_id != u.id)) or \
                (u.role == DEPT_ADMIN and exam.course.department_id != u.department_id):
            raise PermissionDenied("Not allowed for this course.")
        enrolled = set(Enrollment.objects.filter(course=exam.course).values_list("student_id", flat=True))
        n = 0
        with transaction.atomic():
            for rec in request.data.get("records", []):
                sid, m = rec.get("student"), rec.get("marks_obtained")
                if m in (None, ""):
                    continue
                if sid not in enrolled:
                    raise ValidationError(f"Student {sid} is not enrolled.")
                if not (0 <= float(m) <= float(exam.total_marks)):
                    raise ValidationError(f"Marks for student {sid} must be between 0 and {exam.total_marks}.")
                r, _ = Result.objects.get_or_create(exam=exam, student_id=sid, defaults={"marks_obtained": m})
                if float(r.marks_obtained) != float(m):
                    r.marks_obtained = m
                    r.save()
                n += 1
        log_action(request, "BULK_RESULTS", exam, {"saved": n})
        return Response({"saved": n})


class AcademicRecordViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AcademicRecordSerializer
    search_fields = ["student__student_id", "student__user__first_name", "student__user__last_name"]
    filterset_fields = ["student", "semester"]
    ordering_fields = ["gpa", "cgpa", "semester__start_date"]

    def get_queryset(self):
        return AcademicRecord.objects.select_related("student__user", "semester").filter(
            student__in=scoped_students(self.request.user))

    @action(detail=False, methods=["post"], permission_classes=[IsStaff])
    def recalculate(self, request):
        pairs = Result.objects.filter(student__in=scoped_students(request.user)).values_list(
            "student_id", "exam__course__semester_id").distinct()
        from apps.courses.models import Semester
        sems = {s.id: s for s in Semester.objects.all()}
        studs = {s.id: s for s in Student.objects.all()}
        for sid, semid in pairs:
            recalculate_academic_record(studs[sid], sems[semid])
        log_action(request, "RECALCULATE_GPA", model_name="AcademicRecord", changes={"pairs": len(pairs)})
        return Response({"recalculated": len(pairs)})

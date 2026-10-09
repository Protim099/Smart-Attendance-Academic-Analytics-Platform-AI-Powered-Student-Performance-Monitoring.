from django.db.models import Count, Q
from rest_framework import viewsets

from apps.common.mixins import AuditMixin
from apps.common.permissions import (DEPT_ADMIN, SUPER_ADMIN, TEACHER, IsAdminOrReadOnly, IsStaffOrReadOnly,
                                     IsSuperAdminOrReadOnly)
from apps.common.scope import scoped_courses

from .models import ClassSchedule, Course, Department, Program, Semester
from .serializers import (ClassScheduleSerializer, CourseSerializer, DepartmentSerializer, ProgramSerializer,
                          SemesterSerializer)


class DepartmentViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = DepartmentSerializer
    permission_classes = [IsSuperAdminOrReadOnly]
    search_fields = ["name", "code"]
    filterset_fields = ["is_active"]
    ordering_fields = ["name", "code"]

    def get_queryset(self):
        qs = Department.objects.annotate(
            students_count=Count("students", distinct=True),
            teachers_count=Count("teachers", distinct=True),
            courses_count=Count("courses", distinct=True),
        ).order_by("name")
        u = self.request.user
        if u.role == DEPT_ADMIN:
            qs = qs.filter(pk=u.department_id)
        return qs


class ProgramViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = ProgramSerializer
    permission_classes = [IsAdminOrReadOnly]
    search_fields = ["name", "code"]
    filterset_fields = ["department", "is_active"]

    def get_queryset(self):
        qs = Program.objects.select_related("department")
        u = self.request.user
        if u.role == DEPT_ADMIN:
            qs = qs.filter(department=u.department)
        return qs


class SemesterViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = SemesterSerializer
    permission_classes = [IsSuperAdminOrReadOnly]
    queryset = Semester.objects.all()
    search_fields = ["name"]
    filterset_fields = ["is_current", "year", "term"]
    ordering_fields = ["start_date", "name"]


class CourseViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = CourseSerializer
    permission_classes = [IsAdminOrReadOnly]
    search_fields = ["code", "title", "teacher__user__first_name", "teacher__user__last_name"]
    filterset_fields = ["department", "program", "semester", "teacher", "is_active"]
    ordering_fields = ["code", "title", "credit_hours"]

    def get_queryset(self):
        return scoped_courses(self.request.user).annotate(
            enrolled_count=Count("enrollments", filter=~Q(enrollments__status="dropped"), distinct=True)
        ).order_by("code")

    def perform_create(self, serializer):
        u = self.request.user
        if u.role == DEPT_ADMIN:
            from rest_framework.exceptions import ValidationError
            if serializer.validated_data["department"] != u.department:
                raise ValidationError({"department": "You can only create courses in your department."})
        super().perform_create(serializer)


class ClassScheduleViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = ClassScheduleSerializer
    permission_classes = [IsStaffOrReadOnly]
    search_fields = ["course__code", "course__title", "room"]
    filterset_fields = ["course", "day_of_week", "course__teacher"]

    def get_queryset(self):
        return ClassSchedule.objects.select_related("course").filter(course__in=scoped_courses(self.request.user))

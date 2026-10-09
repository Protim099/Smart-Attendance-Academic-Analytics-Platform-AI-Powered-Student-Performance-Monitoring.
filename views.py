from django.db.models import Count
from rest_framework import viewsets

from apps.common.mixins import AuditMixin
from apps.common.permissions import DEPT_ADMIN, SUPER_ADMIN, TEACHER, IsAdminOrReadOnly

from .models import Teacher
from .serializers import TeacherSerializer


class TeacherViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = TeacherSerializer
    permission_classes = [IsAdminOrReadOnly]
    search_fields = ["employee_id", "user__first_name", "user__last_name", "user__email", "user__username"]
    filterset_fields = ["department", "designation"]
    ordering_fields = ["employee_id", "user__first_name"]

    def get_queryset(self):
        qs = Teacher.objects.select_related("user", "department").annotate(courses_count=Count("courses", distinct=True)).order_by("employee_id")
        u = self.request.user
        if u.role == DEPT_ADMIN:
            qs = qs.filter(department=u.department)
        elif u.role == TEACHER:
            qs = qs.filter(department=getattr(getattr(u, "teacher_profile", None), "department", None))
        elif u.role == "student":
            qs = qs.filter(courses__enrollments__student__user=u).distinct()
        return qs

    def perform_destroy(self, instance):
        from apps.accounts.audit import log_action
        log_action(self.request, "DELETE", instance)
        user = instance.user
        instance.delete()
        user.delete()

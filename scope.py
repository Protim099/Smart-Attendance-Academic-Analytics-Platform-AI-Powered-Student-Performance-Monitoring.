"""Role-based data scoping helpers — every list endpoint goes through these."""
from apps.common.permissions import DEPT_ADMIN, SUPER_ADMIN, TEACHER


def scoped_students(user):
    from apps.students.models import Student

    qs = Student.objects.select_related("user", "department", "program", "current_semester")
    if user.role == SUPER_ADMIN:
        return qs
    if user.role == DEPT_ADMIN:
        return qs.filter(department=user.department)
    if user.role == TEACHER:
        return qs.filter(enrollments__course__teacher__user=user).exclude(enrollments__status="dropped").distinct()
    return qs.filter(user=user)


def scoped_courses(user):
    from apps.courses.models import Course

    qs = Course.objects.select_related("department", "program", "semester", "teacher__user")
    if user.role == SUPER_ADMIN:
        return qs
    if user.role == DEPT_ADMIN:
        return qs.filter(department=user.department)
    if user.role == TEACHER:
        return qs.filter(teacher__user=user)
    return qs.filter(enrollments__student__user=user).exclude(enrollments__status="dropped").distinct()


def scoped_sessions(user):
    from apps.attendance.models import AttendanceSession

    qs = AttendanceSession.objects.select_related("course", "schedule", "created_by__user")
    if user.role == SUPER_ADMIN:
        return qs
    if user.role == DEPT_ADMIN:
        return qs.filter(course__department=user.department)
    if user.role == TEACHER:
        return qs.filter(course__teacher__user=user)
    return qs.filter(course__enrollments__student__user=user).distinct()


def scoped_attendance(user):
    from apps.attendance.models import Attendance

    qs = Attendance.objects.select_related("session__course", "student__user")
    if user.role == SUPER_ADMIN:
        return qs
    if user.role == DEPT_ADMIN:
        return qs.filter(session__course__department=user.department)
    if user.role == TEACHER:
        return qs.filter(session__course__teacher__user=user)
    return qs.filter(student__user=user)

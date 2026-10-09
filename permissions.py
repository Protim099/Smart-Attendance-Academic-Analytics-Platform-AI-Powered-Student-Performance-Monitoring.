from rest_framework.permissions import SAFE_METHODS, BasePermission

SUPER_ADMIN = "super_admin"
DEPT_ADMIN = "dept_admin"
TEACHER = "teacher"
STUDENT = "student"
ADMIN_ROLES = (SUPER_ADMIN, DEPT_ADMIN)


def roles_required(*roles):
    class _RolePermission(BasePermission):
        def has_permission(self, request, view):
            u = request.user
            return bool(u and u.is_authenticated and u.role in roles)

    _RolePermission.__name__ = "Role_" + "_".join(roles)
    return _RolePermission


IsSuperAdmin = roles_required(SUPER_ADMIN)
IsAdminRole = roles_required(*ADMIN_ROLES)
IsTeacher = roles_required(TEACHER)
IsStudent = roles_required(STUDENT)
IsStaff = roles_required(SUPER_ADMIN, DEPT_ADMIN, TEACHER)


def _read_or_roles(*roles):
    class _P(BasePermission):
        def has_permission(self, request, view):
            u = request.user
            if not (u and u.is_authenticated):
                return False
            return request.method in SAFE_METHODS or u.role in roles

    return _P


IsAdminOrReadOnly = _read_or_roles(*ADMIN_ROLES)
IsSuperAdminOrReadOnly = _read_or_roles(SUPER_ADMIN)
IsStaffOrReadOnly = _read_or_roles(SUPER_ADMIN, DEPT_ADMIN, TEACHER)

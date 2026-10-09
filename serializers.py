from rest_framework import serializers

from apps.common.serializers import ProfileSerializer

from .models import Teacher


class TeacherSerializer(ProfileSerializer):
    user_role = "teacher"

    department_name = serializers.CharField(source="department.name", read_only=True)
    courses_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Teacher
        fields = ["id", "employee_id", "username", "email", "first_name", "last_name", "full_name", "phone",
                  "password", "is_active", "department", "department_name", "designation", "office_room",
                  "courses_count", "created_at"]

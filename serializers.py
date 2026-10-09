from django.db.models import Q
from rest_framework import serializers

from apps.common.permissions import TEACHER

from .models import ClassSchedule, Course, Department, Program, Semester


class DepartmentSerializer(serializers.ModelSerializer):
    students_count = serializers.IntegerField(read_only=True)
    teachers_count = serializers.IntegerField(read_only=True)
    courses_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name", "code", "description", "is_active", "students_count", "teachers_count",
                  "courses_count", "created_at", "updated_at"]


class ProgramSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = Program
        fields = ["id", "name", "code", "department", "department_name", "duration_semesters", "is_active",
                  "created_at"]


class SemesterSerializer(serializers.ModelSerializer):
    term_display = serializers.CharField(source="get_term_display", read_only=True)

    class Meta:
        model = Semester
        fields = ["id", "name", "year", "term", "term_display", "start_date", "end_date", "is_current"]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_date": "End date must be after start date."})
        return attrs


class CourseSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    program_name = serializers.CharField(source="program.name", read_only=True, default=None)
    semester_name = serializers.CharField(source="semester.name", read_only=True)
    teacher_name = serializers.CharField(source="teacher.user.get_full_name", read_only=True, default=None)
    enrolled_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Course
        fields = ["id", "code", "title", "credit_hours", "department", "department_name", "program", "program_name",
                  "semester", "semester_name", "teacher", "teacher_name", "enrolled_count", "is_active",
                  "created_at"]


class ClassScheduleSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    day_name = serializers.CharField(source="get_day_of_week_display", read_only=True)

    class Meta:
        model = ClassSchedule
        fields = ["id", "course", "course_code", "course_title", "day_of_week", "day_name", "start_time",
                  "end_time", "room"]

    def validate(self, attrs):
        g = lambda k: attrs.get(k, getattr(self.instance, k, None))  # noqa: E731
        course, day, start, end, room = g("course"), g("day_of_week"), g("start_time"), g("end_time"), g("room")
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "End time must be after start time."})
        user = self.context["request"].user
        if user.role == TEACHER and course and (not course.teacher or course.teacher.user_id != user.id):
            raise serializers.ValidationError({"course": "You can only schedule classes for your own courses."})
        if room and start and end:
            clash = ClassSchedule.objects.filter(day_of_week=day, room=room, start_time__lt=end, end_time__gt=start)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"room": "This room is already booked for the selected time."})
        if course and course.teacher_id and start and end:
            clash = ClassSchedule.objects.filter(course__teacher_id=course.teacher_id, day_of_week=day,
                                                 start_time__lt=end, end_time__gt=start)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError("The teacher already has a class at this time.")
        return attrs

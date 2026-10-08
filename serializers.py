from django.utils import timezone
from rest_framework import serializers

from apps.common.permissions import SUPER_ADMIN, TEACHER
from apps.students.models import Enrollment

from .models import Attendance, AttendanceSession, CorrectionRequest


class SessionSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    teacher_name = serializers.CharField(source="created_by.user.get_full_name", read_only=True, default=None)
    qr_active = serializers.SerializerMethodField()
    present_count = serializers.IntegerField(read_only=True, default=0)
    absent_count = serializers.IntegerField(read_only=True, default=0)
    late_count = serializers.IntegerField(read_only=True, default=0)
    excused_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = AttendanceSession
        fields = ["id", "course", "course_code", "course_title", "schedule", "date", "topic", "teacher_name",
                  "is_locked", "qr_active", "qr_expires_at", "present_count", "absent_count", "late_count",
                  "excused_count", "created_at"]
        read_only_fields = ["qr_expires_at"]

    def get_qr_active(self, obj):
        return bool(obj.qr_token and obj.qr_expires_at and obj.qr_expires_at > timezone.now())

    def validate(self, attrs):
        user = self.context["request"].user
        course = attrs.get("course") or getattr(self.instance, "course", None)
        if user.role == TEACHER and course and (not course.teacher or course.teacher.user_id != user.id):
            raise serializers.ValidationError({"course": "You can only create sessions for your own courses."})
        if user.role == "dept_admin" and course and course.department_id != user.department_id:
            raise serializers.ValidationError({"course": "Course is outside your department."})
        schedule = attrs.get("schedule")
        if schedule and course and schedule.course_id != course.id:
            raise serializers.ValidationError({"schedule": "Schedule does not belong to this course."})
        date = attrs.get("date")
        if date and date > timezone.localdate():
            raise serializers.ValidationError({"date": "Attendance cannot be created for a future date."})
        qs = AttendanceSession.objects.filter(course=course, date=date or getattr(self.instance, "date", None),
                                              schedule=schedule)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if date and qs.exists():
            raise serializers.ValidationError("A session already exists for this course and date.")
        return attrs


class AttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.user.get_full_name", read_only=True)
    student_roll = serializers.CharField(source="student.student_id", read_only=True)
    course = serializers.IntegerField(source="session.course_id", read_only=True)
    course_code = serializers.CharField(source="session.course.code", read_only=True)
    date = serializers.DateField(source="session.date", read_only=True)
    marked_by_name = serializers.CharField(source="marked_by.username", read_only=True, default=None)

    class Meta:
        model = Attendance
        fields = ["id", "session", "course", "course_code", "date", "student", "student_name", "student_roll",
                  "status", "source", "remarks", "marked_by_name", "updated_at"]
        read_only_fields = ["session", "student", "source"]


class BulkRecordSerializer(serializers.Serializer):
    student = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Attendance.Status.choices)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=200)


class BulkMarkSerializer(serializers.Serializer):
    records = BulkRecordSerializer(many=True, allow_empty=False)


class CorrectionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="attendance.student.user.get_full_name", read_only=True)
    student_roll = serializers.CharField(source="attendance.student.student_id", read_only=True)
    course_code = serializers.CharField(source="attendance.session.course.code", read_only=True)
    date = serializers.DateField(source="attendance.session.date", read_only=True)
    current_status = serializers.CharField(source="attendance.status", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.username", read_only=True, default=None)

    class Meta:
        model = CorrectionRequest
        fields = ["id", "attendance", "student_name", "student_roll", "course_code", "date", "current_status",
                  "requested_status", "reason", "status", "reviewed_by_name", "review_note", "reviewed_at",
                  "created_at"]
        read_only_fields = ["status", "reviewed_at"]

    def validate(self, attrs):
        user = self.context["request"].user
        att = attrs.get("attendance")
        if att and user.role == "student":
            if att.student.user_id != user.id:
                raise serializers.ValidationError({"attendance": "You can only request corrections for your own records."})
            if att.status == attrs.get("requested_status"):
                raise serializers.ValidationError({"requested_status": "Requested status is the same as current."})
            if CorrectionRequest.objects.filter(attendance=att, status="pending").exists():
                raise serializers.ValidationError("A pending request already exists for this record.")
        if len(attrs.get("reason", "")) < 10:
            raise serializers.ValidationError({"reason": "Please explain the reason (min 10 characters)."})
        return attrs

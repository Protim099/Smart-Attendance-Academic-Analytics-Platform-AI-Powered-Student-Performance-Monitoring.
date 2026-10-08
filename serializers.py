from django.utils import timezone
from rest_framework import serializers

from apps.common.permissions import DEPT_ADMIN, STUDENT, TEACHER
from apps.students.models import Enrollment

from .models import AcademicRecord, Assignment, Exam, Result, Submission


def _check_course_access(user, course):
    if user.role == TEACHER and (not course.teacher or course.teacher.user_id != user.id):
        raise serializers.ValidationError({"course": "You can only manage your own courses."})
    if user.role == DEPT_ADMIN and course.department_id != user.department_id:
        raise serializers.ValidationError({"course": "Course is outside your department."})


class AssignmentSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    submissions_count = serializers.IntegerField(read_only=True, default=0)
    my_submission = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = ["id", "course", "course_code", "course_title", "title", "description", "due_date", "max_marks",
                  "attachment", "submissions_count", "my_submission", "created_at"]

    def validate(self, attrs):
        if "course" in attrs:
            _check_course_access(self.context["request"].user, attrs["course"])
        if attrs.get("max_marks") is not None and attrs["max_marks"] <= 0:
            raise serializers.ValidationError({"max_marks": "Must be greater than zero."})
        return attrs

    def get_my_submission(self, obj):
        u = self.context["request"].user
        if u.role != STUDENT:
            return None
        s = obj.submissions.filter(student__user=u).first()
        return {"id": s.id, "marks": s.marks, "feedback": s.feedback, "submitted_at": s.submitted_at} if s else None


class SubmissionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.user.get_full_name", read_only=True)
    student_roll = serializers.CharField(source="student.student_id", read_only=True)
    assignment_title = serializers.CharField(source="assignment.title", read_only=True)
    max_marks = serializers.DecimalField(source="assignment.max_marks", max_digits=6, decimal_places=2, read_only=True)
    is_late = serializers.SerializerMethodField()

    class Meta:
        model = Submission
        fields = ["id", "assignment", "assignment_title", "student", "student_name", "student_roll", "text", "file",
                  "marks", "max_marks", "feedback", "submitted_at", "is_late"]
        read_only_fields = ["student", "submitted_at"]

    def get_is_late(self, obj):
        return obj.submitted_at > obj.assignment.due_date

    def validate(self, attrs):
        u = self.context["request"].user
        if u.role == STUDENT:
            for f in ("marks", "feedback"):
                attrs.pop(f, None)
            a = attrs.get("assignment") or self.instance.assignment
            if not Enrollment.objects.filter(student__user=u, course=a.course).exclude(status="dropped").exists():
                raise serializers.ValidationError("You are not enrolled in this course.")
            if not attrs.get("text") and not attrs.get("file") and not self.instance:
                raise serializers.ValidationError("Provide text or attach a file.")
        else:
            a = self.instance.assignment if self.instance else attrs.get("assignment")
            m = attrs.get("marks")
            if m is not None and (m < 0 or m > a.max_marks):
                raise serializers.ValidationError({"marks": f"Marks must be between 0 and {a.max_marks}."})
        return attrs


class ExamSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    type_display = serializers.CharField(source="get_exam_type_display", read_only=True)
    average_percentage = serializers.FloatField(read_only=True, default=None)

    class Meta:
        model = Exam
        fields = ["id", "course", "course_code", "course_title", "title", "exam_type", "type_display", "date",
                  "total_marks", "average_percentage"]

    def validate(self, attrs):
        if "course" in attrs:
            _check_course_access(self.context["request"].user, attrs["course"])
        if attrs.get("total_marks") is not None and attrs["total_marks"] <= 0:
            raise serializers.ValidationError({"total_marks": "Must be greater than zero."})
        return attrs


class ResultSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.user.get_full_name", read_only=True)
    student_roll = serializers.CharField(source="student.student_id", read_only=True)
    exam_title = serializers.CharField(source="exam.title", read_only=True)
    course_code = serializers.CharField(source="exam.course.code", read_only=True)
    total_marks = serializers.DecimalField(source="exam.total_marks", max_digits=6, decimal_places=2, read_only=True)
    percentage = serializers.SerializerMethodField()

    class Meta:
        model = Result
        fields = ["id", "exam", "exam_title", "course_code", "student", "student_name", "student_roll",
                  "marks_obtained", "total_marks", "percentage", "grade", "grade_point", "remarks"]
        read_only_fields = ["grade", "grade_point"]

    def get_percentage(self, obj):
        return round(float(obj.marks_obtained) / float(obj.exam.total_marks) * 100, 1)

    def validate(self, attrs):
        exam = attrs.get("exam") or getattr(self.instance, "exam", None)
        student = attrs.get("student") or getattr(self.instance, "student", None)
        _check_course_access(self.context["request"].user, exam.course)
        m = attrs.get("marks_obtained")
        if m is not None and (m < 0 or m > exam.total_marks):
            raise serializers.ValidationError({"marks_obtained": f"Marks must be between 0 and {exam.total_marks}."})
        if student and not Enrollment.objects.filter(student=student, course=exam.course).exists():
            raise serializers.ValidationError({"student": "Student is not enrolled in this course."})
        if not self.instance and Result.objects.filter(exam=exam, student=student).exists():
            raise serializers.ValidationError("A result already exists for this student and exam.")
        return attrs


class AcademicRecordSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.user.get_full_name", read_only=True)
    student_roll = serializers.CharField(source="student.student_id", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True)

    class Meta:
        model = AcademicRecord
        fields = ["id", "student", "student_name", "student_roll", "semester", "semester_name", "gpa", "cgpa",
                  "credits"]

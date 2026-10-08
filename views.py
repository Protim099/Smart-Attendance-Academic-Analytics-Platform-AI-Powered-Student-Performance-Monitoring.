import base64
import io
import secrets
from datetime import timedelta

import django_filters as df
import qrcode
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.audit import log_action
from apps.accounts.models import AuditLog
from apps.common.mixins import AuditMixin
from apps.common.permissions import ADMIN_ROLES, STUDENT, SUPER_ADMIN, TEACHER, IsStaff, IsStaffOrReadOnly, IsStudent
from apps.common.scope import scoped_attendance, scoped_courses, scoped_sessions, scoped_students
from apps.notifications.services import notify
from apps.students.models import Enrollment, Student

from .models import Attendance, AttendanceSession, CorrectionRequest
from .serializers import (AttendanceSerializer, BulkMarkSerializer, CorrectionSerializer, SessionSerializer)
from .utils import AGG, finalize, summarize


def _ensure_can_manage(user, course):
    if user.role == TEACHER and (not course.teacher or course.teacher.user_id != user.id):
        raise PermissionDenied("You are not the teacher of this course.")
    if user.role == "dept_admin" and course.department_id != user.department_id:
        raise PermissionDenied("Course is outside your department.")


class SessionViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_fields = ["course", "date", "is_locked", "course__semester"]
    search_fields = ["course__code", "course__title", "topic"]
    ordering_fields = ["date", "course__code"]

    def get_queryset(self):
        return scoped_sessions(self.request.user).annotate(
            present_count=Count("records", filter=Q(records__status="present")),
            absent_count=Count("records", filter=Q(records__status="absent")),
            late_count=Count("records", filter=Q(records__status="late")),
            excused_count=Count("records", filter=Q(records__status="excused")),
        ).order_by("-date", "-id")

    def perform_create(self, serializer):
        teacher = getattr(self.request.user, "teacher_profile", None)
        instance = serializer.save(created_by=teacher or serializer.validated_data["course"].teacher)
        log_action(self.request, "CREATE", instance)

    def perform_update(self, serializer):
        if serializer.instance.is_locked and self.request.user.role not in ADMIN_ROLES:
            raise PermissionDenied("This session is locked. Ask an administrator to unlock it.")
        super().perform_update(serializer)

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        session = self.get_object()
        enrolled = Student.objects.filter(
            enrollments__course=session.course).exclude(enrollments__status="dropped").select_related("user").distinct()
        records = {r.student_id: r for r in session.records.all()}
        data = []
        for s in enrolled.order_by("student_id"):
            r = records.get(s.id)
            data.append({"student": s.id, "student_roll": s.student_id, "student_name": s.user.get_full_name(),
                         "status": r.status if r else None, "remarks": r.remarks if r else "",
                         "source": r.source if r else None, "record_id": r.id if r else None})
        return Response({"session": SessionSerializer(session, context={"request": request}).data, "students": data})

    @action(detail=True, methods=["post"])
    def mark(self, request, pk=None):
        """Bulk mark / edit attendance. Edits of existing records are audit-tracked."""
        session = self.get_object()
        _ensure_can_manage(request.user, session.course)
        if session.is_locked and request.user.role not in ADMIN_ROLES:
            raise PermissionDenied("This session is locked.")
        s = BulkMarkSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        valid_ids = set(Enrollment.objects.filter(course=session.course).exclude(status="dropped")
                        .values_list("student_id", flat=True))
        existing = {r.student_id: r for r in session.records.all()}
        created = updated = 0
        with transaction.atomic():
            for rec in s.validated_data["records"]:
                sid = rec["student"]
                if sid not in valid_ids:
                    raise ValidationError({"records": f"Student {sid} is not enrolled in this course."})
                remarks = rec.get("remarks", "")
                cur = existing.get(sid)
                if cur is None:
                    Attendance.objects.create(session=session, student_id=sid, status=rec["status"], remarks=remarks,
                                              marked_by=request.user)
                    created += 1
                elif cur.status != rec["status"] or cur.remarks != remarks:
                    log_action(request, "ATTENDANCE_EDIT", cur,
                               {"old_status": cur.status, "new_status": rec["status"], "old_remarks": cur.remarks,
                                "new_remarks": remarks})
                    cur.status, cur.remarks, cur.marked_by = rec["status"], remarks, request.user
                    cur.save()
                    updated += 1
        log_action(request, "ATTENDANCE_MARK", session, {"created": created, "updated": updated})
        return Response({"created": created, "updated": updated})

    @action(detail=True, methods=["post", "delete"])
    def qr(self, request, pk=None):
        """POST: generate a temporary QR code. DELETE: close it immediately."""
        session = self.get_object()
        _ensure_can_manage(request.user, session.course)
        if request.method == "DELETE":
            session.qr_token = session.qr_expires_at = session.qr_created_at = None
            session.save(update_fields=["qr_token", "qr_expires_at", "qr_created_at", "updated_at"])
            return Response(status=status.HTTP_204_NO_CONTENT)
        if session.is_locked:
            raise ValidationError("Session is locked.")
        try:
            minutes = max(1, min(int(request.data.get("minutes", 10)), 120))
        except (TypeError, ValueError):
            minutes = 10
        now = timezone.now()
        session.qr_token = secrets.token_urlsafe(16)
        session.qr_created_at = now
        session.qr_expires_at = now + timedelta(minutes=minutes)
        session.save(update_fields=["qr_token", "qr_expires_at", "qr_created_at", "updated_at"])
        url = f"{settings.FRONTEND_URL.rstrip('/')}/scan?token={session.qr_token}"
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        log_action(request, "QR_GENERATE", session, {"minutes": minutes})
        return Response({"token": session.qr_token, "url": url, "expires_at": session.qr_expires_at,
                         "qr_image": data_url})


class AttendanceFilter(df.FilterSet):
    date_from = df.DateFilter(field_name="session__date", lookup_expr="gte")
    date_to = df.DateFilter(field_name="session__date", lookup_expr="lte")
    course = df.NumberFilter(field_name="session__course_id")
    semester = df.NumberFilter(field_name="session__course__semester_id")

    class Meta:
        model = Attendance
        fields = ["student", "status", "session", "source"]


class AttendanceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                        viewsets.GenericViewSet):
    """List/filter attendance records; staff can edit (audit-tracked)."""

    serializer_class = AttendanceSerializer
    permission_classes = [IsStaffOrReadOnly]
    filterset_class = AttendanceFilter
    search_fields = ["student__student_id", "student__user__first_name", "student__user__last_name", "session__course__code"]
    ordering_fields = ["session__date", "status", "student__student_id"]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return scoped_attendance(self.request.user)

    def perform_update(self, serializer):
        inst = serializer.instance
        _ensure_can_manage(self.request.user, inst.session.course)
        if inst.session.is_locked and self.request.user.role not in ADMIN_ROLES:
            raise PermissionDenied("This session is locked.")
        old = {"status": inst.status, "remarks": inst.remarks}
        obj = serializer.save(marked_by=self.request.user)
        log_action(self.request, "ATTENDANCE_EDIT", obj,
                   {"old_status": old["status"], "new_status": obj.status, "old_remarks": old["remarks"],
                    "new_remarks": obj.remarks})

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        obj = self.get_object()
        logs = AuditLog.objects.filter(model_name="Attendance", object_id=str(obj.pk)).select_related("user")[:50]
        return Response([{"id": l.id, "user": l.user.username if l.user else "system", "action": l.action,
                          "changes": l.changes, "created_at": l.created_at} for l in logs])

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """by=course (student-wise), by=student (course-wise roster), by=date (daily/date-wise), by=month."""
        by = request.query_params.get("by", "course")
        qs = AttendanceFilter(request.query_params, queryset=self.get_queryset()).qs
        user = request.user
        if by == "course":
            if user.role != STUDENT and not request.query_params.get("student"):
                raise ValidationError({"student": "Student is required."})
            rows = qs.values(code=F("session__course__code"), title=F("session__course__title"),
                             cid=F("session__course_id")).annotate(**AGG).order_by("code")
        elif by == "student":
            rows = qs.values(roll=F("student__student_id"), sid=F("student_id"),
                             name=F("student__user__first_name"), last=F("student__user__last_name")
                             ).annotate(**AGG).order_by("roll")
        elif by == "month":
            rows = qs.annotate(month=TruncMonth("session__date")).values("month").annotate(**AGG).order_by("month")
        else:
            rows = qs.values(date=F("session__date")).annotate(**AGG).order_by("-date")[:120]
        out = [finalize(dict(r)) for r in rows]
        return Response({"overall": summarize(qs), "rows": out, "threshold": settings.LOW_ATTENDANCE_THRESHOLD})

    @action(detail=False, methods=["get"], url_path="low-attendance", permission_classes=[IsStaff])
    def low_attendance(self, request):
        try:
            threshold = float(request.query_params.get("threshold", settings.LOW_ATTENDANCE_THRESHOLD))
        except ValueError:
            threshold = settings.LOW_ATTENDANCE_THRESHOLD
        qs = AttendanceFilter(request.query_params, queryset=self.get_queryset()).qs
        rows = (qs.values(roll=F("student__student_id"), sid=F("student_id"), first=F("student__user__first_name"),
                          last=F("student__user__last_name"), code=F("session__course__code"),
                          cid=F("session__course_id")).annotate(**AGG))
        out = []
        for r in rows:
            r = finalize(dict(r))
            if r["percentage"] is not None and r["percentage"] < threshold:
                r["name"] = f"{r.pop('first')} {r.pop('last')}".strip()
                out.append(r)
        out.sort(key=lambda r: r["percentage"])
        return Response({"threshold": threshold, "count": len(out), "results": out})

    @action(detail=False, methods=["post"], url_path="send-warnings", permission_classes=[IsStaff])
    def send_warnings(self, request):
        data = self.low_attendance(request).data
        sent = 0
        for r in data["results"]:
            st = Student.objects.select_related("user").get(pk=r["sid"])
            notify(st.user, "Low attendance warning",
                   f"Your attendance in {r['code']} is {r['percentage']}%, below the required {data['threshold']:.0f}%.",
                   "warning", "/my-attendance")
            sent += 1
        log_action(request, "SEND_WARNINGS", model_name="Attendance", changes={"sent": sent})
        return Response({"sent": sent})


class ScanView(APIView):
    """Student scans the teacher's temporary QR code."""

    permission_classes = [IsStudent]
    throttle_scope = "scan"

    def get_throttles(self):
        from rest_framework.throttling import ScopedRateThrottle
        return [ScopedRateThrottle()]

    def post(self, request):
        token = (request.data.get("token") or "").strip()
        session = AttendanceSession.objects.select_related("course").filter(qr_token=token).first() if token else None
        if not session or not session.qr_expires_at or session.qr_expires_at < timezone.now():
            return Response({"detail": "This QR code is invalid or has expired."}, status=status.HTTP_400_BAD_REQUEST)
        student = getattr(request.user, "student_profile", None)
        if not student or not Enrollment.objects.filter(student=student, course=session.course).exclude(
                status="dropped").exists():
            return Response({"detail": "You are not enrolled in this course."}, status=status.HTTP_403_FORBIDDEN)
        late_after = timedelta(minutes=settings.QR_LATE_AFTER_MINUTES)
        new_status = "late" if timezone.now() - session.qr_created_at > late_after else "present"
        rec, created = Attendance.objects.get_or_create(
            session=session, student=student,
            defaults={"status": new_status, "source": "qr", "marked_by": request.user})
        if not created:
            return Response({"detail": f"Attendance already recorded as {rec.status}.", "status": rec.status})
        log_action(request, "QR_SCAN", rec, {"status": new_status})
        return Response({"detail": f"Attendance recorded for {session.course.code}.", "status": new_status,
                         "course": session.course.code}, status=status.HTTP_201_CREATED)


class CorrectionViewSet(AuditMixin, viewsets.ModelViewSet):
    serializer_class = CorrectionSerializer
    filterset_fields = ["status", "attendance__session__course"]
    search_fields = ["attendance__student__student_id", "attendance__student__user__first_name", "reason"]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = CorrectionRequest.objects.select_related("attendance__student__user", "attendance__session__course",
                                                      "reviewed_by")
        u = self.request.user
        if u.role == SUPER_ADMIN:
            return qs
        if u.role == "dept_admin":
            return qs.filter(attendance__session__course__department=u.department)
        if u.role == TEACHER:
            return qs.filter(attendance__session__course__teacher__user=u)
        return qs.filter(requested_by=u)

    def get_permissions(self):
        from rest_framework.permissions import IsAuthenticated
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        if self.request.user.role != STUDENT:
            raise PermissionDenied("Only students can submit correction requests.")
        obj = serializer.save(requested_by=self.request.user)
        log_action(self.request, "CORRECTION_REQUEST", obj)
        teacher = obj.attendance.session.course.teacher
        if teacher:
            notify(teacher.user, "Attendance correction request",
                   f"{obj.attendance.student.user.get_full_name()} requested a correction for "
                   f"{obj.attendance.session.course.code} ({obj.attendance.session.date}).", "info",
                   "/attendance")

    def perform_destroy(self, instance):
        if instance.requested_by_id != self.request.user.id or instance.status != "pending":
            raise PermissionDenied("Only your own pending requests can be withdrawn.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        obj = self.get_object()
        if request.user.role == STUDENT:
            raise PermissionDenied("Not allowed.")
        _ensure_can_manage(request.user, obj.attendance.session.course)
        if obj.status != "pending":
            raise ValidationError("This request has already been reviewed.")
        decision = request.data.get("decision")
        if decision not in ("approve", "reject"):
            raise ValidationError({"decision": "Must be 'approve' or 'reject'."})
        with transaction.atomic():
            if decision == "approve":
                att = obj.attendance
                log_action(request, "ATTENDANCE_EDIT", att, {"old_status": att.status,
                           "new_status": obj.requested_status, "via": "correction_request"})
                att.status, att.marked_by = obj.requested_status, request.user
                att.save()
            obj.status = "approved" if decision == "approve" else "rejected"
            obj.reviewed_by, obj.reviewed_at = request.user, timezone.now()
            obj.review_note = str(request.data.get("note", ""))[:250]
            obj.save()
        log_action(request, "CORRECTION_REVIEW", obj, {"decision": decision})
        notify(obj.requested_by, f"Correction request {obj.status}",
               f"Your request for {obj.attendance.session.course.code} on {obj.attendance.session.date} was {obj.status}.",
               "success" if decision == "approve" else "danger", "/my-attendance")
        return Response(self.get_serializer(obj).data)

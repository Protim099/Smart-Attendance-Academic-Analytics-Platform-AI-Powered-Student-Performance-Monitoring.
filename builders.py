"""Report builders. Each returns {title, subtitle, summary: [(label, value)], columns, rows}."""
from django.conf import settings
from django.db.models import Avg, Count, F
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.academics.models import AcademicRecord, Result
from apps.analytics.models import RiskPrediction
from apps.analytics.services import EXAM_PCT
from apps.attendance.utils import AGG, finalize, summarize
from apps.common.permissions import DEPT_ADMIN, STUDENT, SUPER_ADMIN
from apps.common.scope import scoped_attendance, scoped_courses, scoped_students
from apps.courses.models import Department, Semester
from apps.students.models import Enrollment


def _student(user, pk):
    qs = scoped_students(user)
    if user.role == STUDENT:
        return qs.first() or _nf("Student profile")
    if not pk:
        raise ValidationError({"student": "Student is required."})
    return qs.filter(pk=pk).first() or _nf("Student")


def _course(user, pk):
    if not pk:
        raise ValidationError({"course": "Course is required."})
    return scoped_courses(user).filter(pk=pk).first() or _nf("Course")


def _nf(what):
    raise NotFound(f"{what} not found or not accessible.")


def student_attendance(user, p):
    st = _student(user, p.get("student"))
    qs = scoped_attendance(user).filter(student=st)
    if p.get("course"):
        qs = qs.filter(session__course_id=p["course"])
    if p.get("date_from"):
        qs = qs.filter(session__date__gte=p["date_from"])
    if p.get("date_to"):
        qs = qs.filter(session__date__lte=p["date_to"])
    s = summarize(qs)
    rows = [[str(r.session.date), r.session.course.code, r.session.topic or "-", r.status.title(), r.remarks or "-"]
            for r in qs.order_by("-session__date")[:2000]]
    return {"title": "Student Attendance Report", "subtitle": f"{st.student_id} - {st.user.get_full_name()}",
            "summary": [("Total classes", s["total"]), ("Present", s["present"]), ("Absent", s["absent"]),
                        ("Late", s["late"]), ("Excused", s["excused"]),
                        ("Attendance %", s["percentage"] if s["percentage"] is not None else "-")],
            "columns": ["Date", "Course", "Topic", "Status", "Remarks"], "rows": rows}


def course_attendance(user, p):
    c = _course(user, p.get("course"))
    qs = scoped_attendance(user).filter(session__course=c)
    if p.get("date_from"):
        qs = qs.filter(session__date__gte=p["date_from"])
    if p.get("date_to"):
        qs = qs.filter(session__date__lte=p["date_to"])
    rows = []
    for r in qs.values(roll=F("student__student_id"), first=F("student__user__first_name"),
                       last=F("student__user__last_name")).annotate(**AGG).order_by("roll"):
        r = finalize(dict(r))
        rows.append([r["roll"], f"{r['first']} {r['last']}".strip(), r["present"], r["absent"], r["late"],
                     r["excused"], r["total"], r["percentage"] if r["percentage"] is not None else "-",
                     "LOW" if r["low"] else "OK"])
    s = summarize(qs)
    return {"title": "Course Attendance Report", "subtitle": f"{c.code} - {c.title} ({c.semester.name})",
            "summary": [("Sessions", c.sessions.count()), ("Enrolled", c.enrollments.exclude(status='dropped').count()),
                        ("Overall attendance %", s["percentage"] if s["percentage"] is not None else "-"),
                        ("Low-attendance threshold", f"{settings.LOW_ATTENDANCE_THRESHOLD:.0f}%")],
            "columns": ["Roll", "Student", "Present", "Absent", "Late", "Excused", "Total", "Attendance %", "Status"],
            "rows": rows}


def semester_academic(user, p):
    sem = Semester.objects.filter(pk=p.get("semester")).first() or Semester.objects.filter(is_current=True).first()
    if not sem:
        raise ValidationError({"semester": "Semester is required."})
    studs = scoped_students(user)
    recs = AcademicRecord.objects.filter(semester=sem, student__in=studs).select_related("student__user")
    att = {r["student_id"]: finalize(dict(r)) for r in scoped_attendance(user).filter(
        session__course__semester=sem).values("student_id").annotate(**AGG)}
    rows = []
    for r in recs.order_by("-gpa"):
        a = att.get(r.student_id, {}).get("percentage")
        rows.append([r.student.student_id, r.student.user.get_full_name(), float(r.credits), float(r.gpa),
                     float(r.cgpa), a if a is not None else "-"])
    avg = recs.aggregate(a=Avg("gpa"))["a"]
    return {"title": "Semester Academic Report", "subtitle": sem.name,
            "summary": [("Students", len(rows)), ("Average GPA", round(float(avg), 2) if avg else "-")],
            "columns": ["Roll", "Student", "Credits", "GPA", "CGPA", "Attendance %"], "rows": rows}


def student_performance(user, p):
    st = _student(user, p.get("student"))
    res = Result.objects.filter(student=st).select_related("exam__course").order_by("exam__course__code", "exam__date")
    rows = [[r.exam.course.code, r.exam.title, r.exam.get_exam_type_display(), float(r.marks_obtained),
             float(r.exam.total_marks), round(float(r.marks_obtained) / float(r.exam.total_marks) * 100, 1), r.grade]
            for r in res]
    rec = AcademicRecord.objects.filter(student=st).order_by("-semester__start_date").first()
    pred = RiskPrediction.objects.filter(student=st).order_by("-semester__start_date").first()
    return {"title": "Student Performance Report", "subtitle": f"{st.student_id} - {st.user.get_full_name()}",
            "summary": [("Latest GPA", float(rec.gpa) if rec else "-"), ("CGPA", float(rec.cgpa) if rec else "-"),
                        ("Risk level (estimate)", pred.risk_level.title() if pred else "-")],
            "columns": ["Course", "Exam", "Type", "Marks", "Total", "%", "Grade"], "rows": rows}


def at_risk(user, p):
    qs = RiskPrediction.objects.filter(student__in=scoped_students(user), risk_level__in=["high", "medium"]) \
        .select_related("student__user", "student__department").order_by("-risk_score")
    rows = [[r.student.student_id, r.student.user.get_full_name(), r.student.department.code, r.risk_score,
             r.risk_level.title(), f"{r.academic_risk_probability:.0%}", f"{r.low_attendance_probability:.0%}",
             ", ".join(f["label"] for f in r.factors if f["impact"] > 0)[:80]] for r in qs]
    return {"title": "At-Risk Students Report",
            "subtitle": "Statistical estimates - not guaranteed outcomes; use for supportive intervention only.",
            "summary": [("Students flagged", len(rows)), ("High risk", sum(1 for r in qs if r.risk_level == "high"))],
            "columns": ["Roll", "Student", "Dept", "Risk score", "Level", "Academic risk", "Low-att. risk", "Key factors"],
            "rows": rows}


def department_analytics(user, p):
    dept_id = p.get("department") or (user.department_id if user.role == DEPT_ADMIN else None)
    dept = Department.objects.filter(pk=dept_id).first()
    if not dept or (user.role == DEPT_ADMIN and dept.id != user.department_id):
        raise ValidationError({"department": "Valid department is required."})
    courses = scoped_courses(user).filter(department=dept)
    att = {r["session__course_id"]: finalize(dict(r)) for r in scoped_attendance(user).filter(
        session__course__department=dept).values("session__course_id").annotate(**AGG)}
    ex = {r["exam__course_id"]: r["a"] for r in Result.objects.filter(exam__course__department=dept)
          .values("exam__course_id").annotate(a=Avg(EXAM_PCT))}
    rows = []
    for c in courses.order_by("code"):
        a = att.get(c.id, {}).get("percentage")
        e = ex.get(c.id)
        rows.append([c.code, c.title, c.semester.name, c.teacher.user.get_full_name() if c.teacher else "-",
                     c.enrollments.exclude(status="dropped").count(), a if a is not None else "-",
                     round(e, 1) if e is not None else "-"])
    studs = scoped_students(user).filter(department=dept)
    return {"title": "Department Analytics Report", "subtitle": f"{dept.code} - {dept.name}",
            "summary": [("Students", studs.count()), ("Teachers", dept.teachers.count()), ("Courses", len(rows)),
                        ("High-risk students", RiskPrediction.objects.filter(student__in=studs, risk_level="high").count())],
            "columns": ["Course", "Title", "Semester", "Teacher", "Enrolled", "Attendance %", "Avg exam %"],
            "rows": rows}


REPORTS = {
    "student-attendance": (student_attendance, "Student attendance", ["student", "course", "date_from", "date_to"], True),
    "course-attendance": (course_attendance, "Course attendance", ["course", "date_from", "date_to"], False),
    "semester-academic": (semester_academic, "Semester academic", ["semester"], False),
    "student-performance": (student_performance, "Student performance", ["student"], True),
    "at-risk": (at_risk, "At-risk students", [], False),
    "department-analytics": (department_analytics, "Department analytics", ["department"], False),
}

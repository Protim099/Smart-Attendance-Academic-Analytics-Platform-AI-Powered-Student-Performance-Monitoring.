from django.conf import settings
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Q
from django.db.models.functions import Cast, TruncMonth

from apps.academics.models import AcademicRecord, Assignment, Exam, Result, Submission
from apps.attendance.models import Attendance
from apps.attendance.utils import AGG, finalize, summarize
from apps.common.permissions import SUPER_ADMIN
from apps.common.scope import scoped_courses, scoped_students
from apps.courses.models import Semester
from apps.teachers.models import Teacher

from .models import RiskPrediction

EXAM_PCT = ExpressionWrapper(Cast("marks_obtained", FloatField()) * 100.0 / Cast("exam__total_marks", FloatField()),
                             output_field=FloatField())
ASSIGN_PCT = ExpressionWrapper(Cast("marks", FloatField()) * 100.0 / Cast("assignment__max_marks", FloatField()),
                               output_field=FloatField())


def _r(v, n=1):
    return None if v is None else round(float(v), n)


def overview(user, course=None, semester=None, student=None):
    students = scoped_students(user)
    courses = scoped_courses(user)
    if semester:
        courses = courses.filter(semester_id=semester)
    if course:
        courses = courses.filter(pk=course)
    if student:
        students = students.filter(pk=student)
    course_ids = list(courses.values_list("id", flat=True))
    student_ids = list(students.values_list("id", flat=True))

    att = Attendance.objects.filter(student_id__in=student_ids, session__course_id__in=course_ids)
    res = Result.objects.filter(student_id__in=student_ids, exam__course_id__in=course_ids)
    subs = Submission.objects.filter(student_id__in=student_ids, assignment__course_id__in=course_ids)
    recs = AcademicRecord.objects.filter(student_id__in=student_ids)

    summary = summarize(att)
    latest_cgpa = {}
    for r in recs.order_by("student_id", "semester__start_date"):
        latest_cgpa[r.student_id] = float(r.cgpa)
    cgpas = list(latest_cgpa.values())

    preds = RiskPrediction.objects.filter(student_id__in=student_ids)
    cur = Semester.objects.filter(is_current=True).first()
    if cur:
        preds = preds.filter(semester=cur)
    preds = preds.select_related("student__user", "student__department")

    by_student = {r["student_id"]: finalize(dict(r)) for r in att.values("student_id").annotate(**AGG)}
    low_students = sum(1 for v in by_student.values() if v["low"])

    cards = {
        "overall_attendance": summary["percentage"],
        "total_students": len(student_ids),
        "total_courses": len(course_ids),
        "average_cgpa": _r(sum(cgpas) / len(cgpas), 2) if cgpas else None,
        "at_risk_students": preds.filter(risk_level="high").count(),
        "low_attendance_students": low_students,
        "assignments_total": Assignment.objects.filter(course_id__in=course_ids).count(),
        "exams_total": Exam.objects.filter(course_id__in=course_ids).count(),
        "threshold": settings.LOW_ATTENDANCE_THRESHOLD,
    }
    if user.role == SUPER_ADMIN:
        cards["total_teachers"] = Teacher.objects.count()

    course_att = {r["session__course__code"]: finalize(dict(r)) for r in
                  att.values("session__course__code").annotate(**AGG)}
    course_exam = {r["exam__course__code"]: r["avg"] for r in
                   res.values("exam__course__code").annotate(avg=Avg(EXAM_PCT))}
    course_assign = {r["assignment__course__code"]: r["avg"] for r in
                     subs.filter(marks__isnull=False).values("assignment__course__code").annotate(avg=Avg(ASSIGN_PCT))}
    codes = sorted(set(course_att) | set(course_exam) | set(course_assign))
    course_comparison = [{"course": c, "attendance": (course_att.get(c) or {}).get("percentage"),
                          "exam": _r(course_exam.get(c)), "assignment": _r(course_assign.get(c))} for c in codes]

    monthly = [{"month": r["month"].strftime("%b %Y"), "percentage": finalize(dict(r))["percentage"]}
               for r in att.annotate(month=TruncMonth("session__date")).values("month").annotate(**AGG).order_by("month")]
    sem_perf = [{"semester": r["semester__name"], "gpa": _r(r["gpa"], 2), "cgpa": _r(r["cgpa"], 2)} for r in
                recs.values("semester__name", "semester__start_date").annotate(gpa=Avg("gpa"), cgpa=Avg("cgpa"))
                .order_by("semester__start_date")]
    grade_dist = [{"grade": r["grade"], "count": r["n"]} for r in res.values("grade").annotate(n=Count("id")).order_by("grade")]
    status_dist = [{"name": k.title(), "value": summary[k]} for k in ("present", "absent", "late", "excused")]
    exam_perf = [{"exam": f"{r['exam__course__code']} {r['exam__title']}", "average": _r(r["avg"])} for r in
                 res.values("exam_id", "exam__course__code", "exam__title", "exam__date").annotate(avg=Avg(EXAM_PCT))
                 .order_by("exam__date")[:14]]
    assign_perf = [{"assignment": f"{r['assignment__course__code']} {r['assignment__title'][:18]}", "average": _r(r["avg"])}
                   for r in subs.filter(marks__isnull=False).values("assignment_id", "assignment__course__code",
                   "assignment__title", "assignment__due_date").annotate(avg=Avg(ASSIGN_PCT)).order_by("assignment__due_date")[:14]]

    exam_by_student = {r["student_id"]: r["avg"] for r in res.values("student_id").annotate(avg=Avg(EXAM_PCT))}
    scatter = [{"student": sid, "attendance": by_student[sid]["percentage"], "exam": _r(exam_by_student[sid])}
               for sid in by_student if sid in exam_by_student and by_student[sid]["percentage"] is not None][:400]

    names = {s.id: s for s in students}
    top = sorted(latest_cgpa.items(), key=lambda kv: -kv[1])[:5]
    top_students = [{"id": sid, "roll": names[sid].student_id, "name": names[sid].user.get_full_name(), "cgpa": cg}
                    for sid, cg in top if sid in names]
    at_risk = [{"id": p.student_id, "roll": p.student.student_id, "name": p.student.user.get_full_name(),
                "risk_score": p.risk_score, "risk_level": p.risk_level, "explanation": p.explanation}
               for p in preds.filter(risk_level__in=["high", "medium"]).order_by("-risk_score")[:8]]

    data = {"cards": cards, "attendance_by_course": [{"course": c, "percentage": (course_att.get(c) or {}).get("percentage")}
            for c in sorted(course_att)], "monthly_trend": monthly, "status_distribution": status_dist,
            "semester_performance": sem_perf, "grade_distribution": grade_dist, "exam_performance": exam_perf,
            "assignment_performance": assign_perf, "course_comparison": course_comparison,
            "attendance_vs_performance": scatter, "top_students": top_students, "at_risk_students": at_risk}
    if len(student_ids) == 1:
        p = preds.first()
        data["my_prediction"] = None if not p else {"risk_level": p.risk_level, "risk_score": p.risk_score,
                                                    "explanation": p.explanation, "factors": p.factors}
        data["pending_assignments"] = Assignment.objects.filter(course_id__in=course_ids).exclude(
            submissions__student_id=student_ids[0]).count()
        data["course_attendance"] = [{"course": c, **course_att[c]} for c in sorted(course_att)]
    return data

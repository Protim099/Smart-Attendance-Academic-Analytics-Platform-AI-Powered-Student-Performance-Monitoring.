from django.db.models import Sum

from .models import AcademicRecord, Result, grade_for


def recalculate_academic_record(student, semester):
    rows = (Result.objects.filter(student=student, exam__course__semester=semester)
            .values("exam__course_id", "exam__course__credit_hours")
            .annotate(got=Sum("marks_obtained"), total=Sum("exam__total_marks")))
    pts = credits = 0.0
    for r in rows:
        if not r["total"]:
            continue
        _, gp = grade_for(float(r["got"]) / float(r["total"]) * 100)
        ch = float(r["exam__course__credit_hours"])
        pts += gp * ch
        credits += ch
    if credits == 0:
        AcademicRecord.objects.filter(student=student, semester=semester).delete()
    else:
        AcademicRecord.objects.update_or_create(student=student, semester=semester,
                                                defaults={"gpa": round(pts / credits, 2), "credits": credits})
    # cumulative CGPA across semesters (chronological)
    cum_pts = cum_cr = 0.0
    for rec in AcademicRecord.objects.filter(student=student).order_by("semester__start_date"):
        cum_pts += float(rec.gpa) * float(rec.credits)
        cum_cr += float(rec.credits)
        new = round(cum_pts / cum_cr, 2) if cum_cr else 0
        if float(rec.cgpa) != new:
            AcademicRecord.objects.filter(pk=rec.pk).update(cgpa=new)

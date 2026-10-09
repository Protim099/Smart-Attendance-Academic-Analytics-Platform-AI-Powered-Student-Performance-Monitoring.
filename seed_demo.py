"""Seed realistic demo data: python manage.py seed_demo [--students 60] [--reset]"""
import random
from datetime import datetime, time, timedelta

import numpy as np
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.academics.models import Assignment, Exam, Result, Submission
from apps.accounts.models import User
from apps.attendance.models import Attendance, AttendanceSession
from apps.courses.models import ClassSchedule, Course, Department, Program, Semester
from apps.notifications.services import notify
from apps.students.models import Enrollment, Student
from apps.teachers.models import Teacher

FIRST = ["Rahim", "Karim", "Nusrat", "Sadia", "Tanvir", "Mahmud", "Farhana", "Imran", "Sumaiya", "Rakib", "Nabila",
         "Arif", "Tasnim", "Shafiq", "Mitu", "Jahid", "Lamia", "Fahim", "Maliha", "Sajid", "Rafiq", "Anika", "Hasan",
         "Puja", "Ovi"]
LAST = ["Ahmed", "Hossain", "Rahman", "Islam", "Chowdhury", "Khan", "Akter", "Uddin", "Sarker", "Mia", "Das", "Biswas"]
COURSES = [("CSE101", "Introduction to Programming", 3.0), ("CSE102", "Data Structures", 3.0),
           ("CSE201", "Algorithms", 3.0), ("CSE203", "Database Systems", 3.0), ("CSE205", "Computer Networks", 3.0)]
PREV = [("CSE001", "Discrete Mathematics", 3.0), ("CSE002", "Digital Logic Design", 3.0),
        ("CSE003", "Physics for Computing", 3.0)]


def term_of(d):
    return ("spring" if d.month <= 4 else "summer" if d.month <= 8 else "fall")


class Command(BaseCommand):
    help = "Create demo users, courses, attendance, assignments, exams, results and ML predictions."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=60)
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data first")

    @transaction.atomic
    def handle(self, *args, **o):
        if o["reset"]:
            for m in (Attendance, AttendanceSession, Result, Exam, Submission, Assignment, Enrollment, ClassSchedule,
                      Course, Student, Teacher, Program, Semester, Department):
                m.objects.all().delete()
            User.objects.exclude(is_superuser=True).delete()
        elif Department.objects.exists():
            self.stdout.write(self.style.WARNING("Data already exists. Use --reset to re-seed."))
            return
        rnd, np_rnd = random.Random(42), np.random.default_rng(42)
        today = timezone.localdate()

        cse = Department.objects.create(name="Computer Science & Engineering", code="CSE",
                                        description="Department of CSE")
        eee = Department.objects.create(name="Electrical & Electronic Engineering", code="EEE")
        prog = Program.objects.create(department=cse, name="BSc in CSE", code="BSC-CSE", duration_semesters=8)
        Program.objects.create(department=eee, name="BSc in EEE", code="BSC-EEE", duration_semesters=8)

        prev_start = today - timedelta(days=300)
        cur_start = today - timedelta(days=100)
        prev = Semester.objects.create(name=f"{term_of(prev_start).title()} {prev_start.year}", year=prev_start.year,
                                       term=term_of(prev_start), start_date=prev_start,
                                       end_date=prev_start + timedelta(days=120))
        cur = Semester.objects.create(name=f"{term_of(cur_start).title()} {cur_start.year}", year=cur_start.year,
                                      term=term_of(cur_start), start_date=cur_start,
                                      end_date=cur_start + timedelta(days=150), is_current=True)

        def mkuser(username, role, first, last, pwd, dept=None):
            u = User(username=username, email=f"{username}@university.edu", first_name=first, last_name=last,
                     role=role, department=dept)
            u.set_password(pwd)
            u.save()
            return u

        admin = User.objects.filter(username="admin").first() or mkuser("admin", "super_admin", "System", "Admin",
                                                                          "Admin@12345")
        admin.role, admin.is_superuser, admin.is_staff = "super_admin", True, True
        admin.set_password("Admin@12345")
        admin.save()
        mkuser("cse_admin", "dept_admin", "Dr. Selina", "Haque", "Dept@12345", cse)

        teachers = []
        tnames = [("Dr. Mizanur", "Rahman", "Professor"), ("Farzana", "Karim", "Associate Professor"),
                  ("Anwar", "Hossain", "Assistant Professor"), ("Shirin", "Akter", "Lecturer"),
                  ("Tahmid", "Islam", "Lecturer")]
        for i, (f, l, d) in enumerate(tnames, 1):
            u = mkuser(f"teacher{i}", "teacher", f, l, "Teacher@12345")
            teachers.append(Teacher.objects.create(user=u, employee_id=f"T{1000 + i}", department=cse, designation=d,
                                                   office_room=f"B-{200 + i}"))

        def mk_courses(items, sem, offset=0):
            out = []
            for i, (code, title, cr) in enumerate(items):
                out.append(Course.objects.create(code=code, title=title, credit_hours=cr, department=cse, program=prog,
                                                 semester=sem, teacher=teachers[(i + offset) % len(teachers)]))
            return out

        prev_courses, courses = mk_courses(PREV, prev), mk_courses(COURSES, cur)

        # students with latent traits
        n = o["students"]
        students, traits = [], {}
        used = set()
        for i in range(1, n + 1):
            while True:
                f, l = rnd.choice(FIRST), rnd.choice(LAST)
                if (f, l) not in used:
                    used.add((f, l))
                    break
            u = mkuser(f"student{i}", "student", f, l, "Student@12345")
            s = Student.objects.create(user=u, student_id=f"{cur_start.year % 100}{1000 + i}", department=cse,
                                       program=prog, current_semester=cur, admission_year=cur_start.year - 1,
                                       guardian_phone=f"01700{100000 + i}")
            students.append(s)
            ability = float(np.clip(np_rnd.normal(0.62, 0.2), 0.15, 0.98))
            att_p = float(np.clip(0.45 + 0.4 * ability + np_rnd.normal(0, 0.12), 0.3, 0.99))
            traits[s.id] = (ability, att_p)

        def exam_pct(ab, ap):
            return float(np.clip(15 + 50 * ab + 30 * ap + np_rnd.normal(0, 6), 5, 100))

        # previous semester: results only (gives previous CGPA)
        for c in prev_courses:
            ex = Exam.objects.create(course=c, title="Final", exam_type="final", date=prev.end_date - timedelta(days=5),
                                     total_marks=100)
            for s in students:
                Enrollment.objects.create(student=s, course=c, status="completed")
                ab, ap = traits[s.id]
                Result(exam=ex, student=s, marks_obtained=round(exam_pct(ab, ap), 1)).save()

        # current semester
        days = [(0, time(9, 0), time(10, 30)), (1, time(11, 0), time(12, 30)), (2, time(9, 0), time(10, 30)),
                (3, time(14, 0), time(15, 30)), (4, time(11, 0), time(12, 30))]
        now = timezone.now()
        for ci, c in enumerate(courses):
            for s in students:
                Enrollment.objects.create(student=s, course=c)
            d, st, en = days[ci]
            sch = ClassSchedule.objects.create(course=c, day_of_week=d, start_time=st, end_time=en, room=f"R-{301 + ci}")
            ClassSchedule.objects.create(course=c, day_of_week=(d + 3) % 7 if (d + 3) % 7 < 5 else 2,
                                         start_time=time(16, 0), end_time=time(17, 30), room=f"R-{401 + ci}")
            last = today - timedelta(days=2)
            while last.weekday() != d:
                last -= timedelta(days=1)
            dates = [last - timedelta(weeks=w) for w in range(13, -1, -1)]
            for di, dt in enumerate(dates):
                sess = AttendanceSession.objects.create(course=c, schedule=sch, date=dt, created_by=c.teacher,
                                                        topic=f"Lecture {di + 1}", is_locked=di < 8)
                recs = []
                for s in students:
                    ab, ap = traits[s.id]
                    p = ap - 0.12 * (di / 13) * (1 - ap) * 2  # slight drift for low-attendance students
                    r = rnd.random()
                    status = ("absent" if r > p else "late" if r < 0.07 else "excused" if r > p - 0.03 else "present")
                    recs.append(Attendance(session=sess, student=s, status=status, marked_by=c.teacher.user))
                Attendance.objects.bulk_create(recs)
            # assignments
            for ai in range(3):
                due = now - timedelta(days=75 - ai * 25)
                a = Assignment.objects.create(course=c, title=f"Assignment {ai + 1}", description="Solve the problems.",
                                              due_date=due, max_marks=20, created_by=c.teacher)
                for s in students:
                    ab, ap = traits[s.id]
                    if rnd.random() < 0.45 + 0.55 * ap:
                        Submission.objects.create(
                            assignment=a, student=s, text="Submitted solution.",
                            marks=round(float(np.clip(20 * (0.25 + 0.7 * ab + np_rnd.normal(0, 0.08)), 0, 20)), 1))
            # exams
            for title, typ, total, ago in (("Quiz 1", "quiz", 20, 60), ("Midterm", "midterm", 60, 35)):
                ex = Exam.objects.create(course=c, title=title, exam_type=typ, total_marks=total,
                                         date=today - timedelta(days=ago))
                for s in students:
                    ab, ap = traits[s.id]
                    Result(exam=ex, student=s, marks_obtained=round(exam_pct(ab, ap) / 100 * total, 1)).save()

        for s in students[:15]:
            notify(s.user, "Welcome", "Welcome to the Smart Attendance platform.", "success", "/")
        from apps.analytics.ml import run_predictions
        out = run_predictions(cur, retrain=True)
        self.stdout.write(self.style.SUCCESS(f"Seeded {n} students. ML: {out.get('levels')}"))
        self.stdout.write("Logins -> admin/Admin@12345 | cse_admin/Dept@12345 | teacher1/Teacher@12345 | "
                          "student1/Student@12345")

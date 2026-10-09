"""End-to-end API smoke tests. Run: python manage.py test tests"""
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient


class ApiSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", students=45, verbosity=0)

    def client_for(self, username, password):
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": username, "password": password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return c

    def test_login_fail_and_rbac(self):
        c = APIClient()
        self.assertEqual(c.post("/api/auth/login/", {"username": "admin", "password": "bad"}, format="json").status_code, 401)
        self.assertEqual(c.get("/api/students/").status_code, 401)
        s = self.client_for("student1", "Student@12345")
        self.assertEqual(s.get("/api/users/").status_code, 403)
        self.assertEqual(s.get("/api/audit-logs/").status_code, 403)
        self.assertEqual(s.get("/api/students/").data["count"], 1)  # sees only self
        self.assertEqual(s.post("/api/departments/", {"name": "X", "code": "X"}, format="json").status_code, 403)

    def test_admin_crud_and_audit(self):
        a = self.client_for("admin", "Admin@12345")
        r = a.post("/api/departments/", {"name": "Physics", "code": "PHY"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertGreater(a.get("/api/audit-logs/?model_name=Department").data["count"], 0)
        dept = a.get("/api/departments/").data["results"][0]["id"]
        prog = a.get("/api/programs/").data["results"][0]
        r = a.post("/api/students/", {"username": "newstu", "email": "n@u.edu", "first_name": "New", "password": "Str0ng!Pass99",
                                      "student_id": "999", "department": prog["department"], "program": prog["id"],
                                      "admission_year": 2025}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.client_for("newstu", "Str0ng!Pass99")

    def test_attendance_flow(self):
        t = self.client_for("teacher1", "Teacher@12345")
        course = t.get("/api/courses/").data["results"][0]
        r = t.post("/api/attendance/sessions/", {"course": course["id"], "date": "2020-01-01", "topic": "Test"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.data["id"]
        roster = t.get(f"/api/attendance/sessions/{sid}/roster/").data["students"]
        self.assertGreater(len(roster), 0)
        recs = [{"student": x["student"], "status": "present"} for x in roster]
        self.assertEqual(t.post(f"/api/attendance/sessions/{sid}/mark/", {"records": recs}, format="json").data["created"], len(recs))
        recs[0]["status"] = "absent"
        self.assertEqual(t.post(f"/api/attendance/sessions/{sid}/mark/", {"records": recs}, format="json").data["updated"], 1)
        rec_id = t.get(f"/api/attendance/records/?session={sid}").data["results"][0]["id"]
        self.assertGreaterEqual(len(t.get(f"/api/attendance/records/{rec_id}/history/").data), 0)
        # QR
        qr = t.post(f"/api/attendance/sessions/{sid}/qr/", {"minutes": 5}, format="json")
        self.assertEqual(qr.status_code, 200)
        self.assertTrue(qr.data["qr_image"].startswith("data:image/png"))
        # teacher cannot manage another teacher's course
        other = [c for c in self.client_for("admin", "Admin@12345").get("/api/courses/?page_size=50").data["results"]
                 if c["teacher"] != course["teacher"]][0]
        self.assertEqual(t.post("/api/attendance/sessions/", {"course": other["id"], "date": "2020-01-02"}, format="json").status_code, 400)
        # summaries
        self.assertEqual(t.get("/api/attendance/records/summary/?by=student&course=%s" % course["id"]).status_code, 200)
        self.assertEqual(t.get("/api/attendance/records/low-attendance/").status_code, 200)

    def test_student_views_and_corrections(self):
        s = self.client_for("student1", "Student@12345")
        summ = s.get("/api/attendance/records/summary/?by=course").data
        self.assertTrue(len(summ["rows"]) >= 1)
        rec = s.get("/api/attendance/records/?status=absent").data["results"][0]
        r = s.post("/api/attendance/corrections/", {"attendance": rec["id"], "requested_status": "excused",
                                                    "reason": "I had a medical appointment."}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        t = self.client_for("admin", "Admin@12345")
        rv = t.post(f"/api/attendance/corrections/{r.data['id']}/review/", {"decision": "approve"}, format="json")
        self.assertEqual(rv.status_code, 200, rv.content)
        self.assertEqual(s.get(f"/api/attendance/records/{rec['id']}/").data["status"], "excused")
        self.assertGreater(s.get("/api/notifications/unread-count/").data["count"], 0)

    def test_academics_analytics_ml_reports(self):
        a = self.client_for("admin", "Admin@12345")
        ov = a.get("/api/analytics/overview/").data
        self.assertIsNotNone(ov["cards"]["overall_attendance"])
        self.assertTrue(ov["course_comparison"] and ov["semester_performance"] and ov["attendance_vs_performance"])
        m = a.get("/api/analytics/predictions/metrics/").data
        self.assertIn("academic", m["metrics"])
        self.assertIsNotNone(m["metrics"]["academic"], m["notes"])
        p = a.get("/api/analytics/predictions/?risk_level=high").data
        self.assertGreater(p["count"], 0)
        self.assertTrue(p["results"][0]["factors"])
        self.assertEqual(a.post("/api/analytics/predictions/run/").status_code, 200)
        f = a.get("/api/analytics/predictions/forecast/").data
        self.assertTrue(f["forecast"])
        course = a.get("/api/courses/").data["results"][0]["id"]
        for slug, q in [("course-attendance", f"course={course}"), ("semester-academic", ""), ("at-risk", ""),
                        ("department-analytics", "department=1"), ("student-performance", "student=1"),
                        ("student-attendance", "student=1")]:
            for fmt, magic in (("pdf", b"%PDF"), ("xlsx", b"PK")):
                r = a.get(f"/api/reports/{slug}/?export={fmt}&{q}")
                self.assertEqual(r.status_code, 200, (slug, r.content[:200]))
                self.assertTrue(r.content.startswith(magic))
        st = self.client_for("student1", "Student@12345")
        self.assertEqual(st.get("/api/reports/at-risk/?export=pdf").status_code, 403)
        self.assertEqual(st.get("/api/reports/student-attendance/?export=pdf").status_code, 200)
        self.assertEqual(st.get("/api/analytics/overview/").data["cards"]["total_students"], 1)

    def test_assignments_results(self):
        t = self.client_for("teacher1", "Teacher@12345")
        course = t.get("/api/courses/").data["results"][0]["id"]
        r = t.post("/api/exams/", {"course": course, "title": "Test", "exam_type": "quiz", "date": "2026-01-01", "total_marks": 10}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        stud = t.get(f"/api/enrollments/?course={course}").data["results"][0]["student"]
        rb = t.post("/api/results/bulk/", {"exam": r.data["id"], "records": [{"student": stud, "marks_obtained": 9}]}, format="json")
        self.assertEqual(rb.status_code, 200, rb.content)
        bad = t.post("/api/results/", {"exam": r.data["id"], "student": stud, "marks_obtained": 99}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client_for("student1", "Student@12345").get("/api/assignments/").status_code, 200)

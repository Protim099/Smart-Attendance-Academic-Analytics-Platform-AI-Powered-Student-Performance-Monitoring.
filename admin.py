from django.contrib import admin

from .models import AcademicRecord, Assignment, Exam, Result, Submission

for m in (Assignment, Submission, Exam, Result, AcademicRecord):
    admin.site.register(m)

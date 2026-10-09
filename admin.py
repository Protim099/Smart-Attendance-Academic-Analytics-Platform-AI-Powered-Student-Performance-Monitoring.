from django.contrib import admin

from .models import ClassSchedule, Course, Department, Program, Semester

for m in (Department, Program, Semester, Course, ClassSchedule):
    admin.site.register(m)

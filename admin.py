from django.contrib import admin

from .models import Attendance, AttendanceSession, CorrectionRequest

admin.site.register(AttendanceSession)
admin.site.register(Attendance)
admin.site.register(CorrectionRequest)

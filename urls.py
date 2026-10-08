from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AttendanceViewSet, CorrectionViewSet, ScanView, SessionViewSet

router = DefaultRouter()
router.register("sessions", SessionViewSet, basename="attendance-session")
router.register("corrections", CorrectionViewSet, basename="attendance-correction")
router.register("records", AttendanceViewSet, basename="attendance-record")
urlpatterns = [path("scan/", ScanView.as_view(), name="attendance-scan")] + router.urls

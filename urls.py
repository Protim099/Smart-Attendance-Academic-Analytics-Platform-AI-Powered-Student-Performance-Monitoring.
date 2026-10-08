from rest_framework.routers import DefaultRouter

from .views import AcademicRecordViewSet, AssignmentViewSet, ExamViewSet, ResultViewSet, SubmissionViewSet

router = DefaultRouter()
router.register("assignments", AssignmentViewSet, basename="assignment")
router.register("submissions", SubmissionViewSet, basename="submission")
router.register("exams", ExamViewSet, basename="exam")
router.register("results", ResultViewSet, basename="result")
router.register("academic-records", AcademicRecordViewSet, basename="academic-record")
urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import ClassScheduleViewSet, CourseViewSet, DepartmentViewSet, ProgramViewSet, SemesterViewSet

router = DefaultRouter()
router.register("departments", DepartmentViewSet, basename="department")
router.register("programs", ProgramViewSet, basename="program")
router.register("semesters", SemesterViewSet, basename="semester")
router.register("courses", CourseViewSet, basename="course")
router.register("schedules", ClassScheduleViewSet, basename="schedule")
urlpatterns = router.urls

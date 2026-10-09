from django.urls import path

from .views import ReportListView, ReportView

urlpatterns = [path("", ReportListView.as_view()), path("<slug:slug>/", ReportView.as_view(), name="report")]

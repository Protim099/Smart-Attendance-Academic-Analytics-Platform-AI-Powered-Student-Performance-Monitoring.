from django.http import HttpResponse
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.audit import log_action
from apps.common.permissions import STUDENT

from .builders import REPORTS
from .renderers import to_pdf, to_xlsx


class ReportListView(APIView):
    def get(self, request):
        out = []
        for slug, (_, label, params, student_ok) in REPORTS.items():
            if request.user.role == STUDENT and not student_ok:
                continue
            out.append({"slug": slug, "label": label, "params": params})
        return Response(out)


class ReportView(APIView):
    """GET /api/reports/<slug>/?format=pdf|xlsx|json&<params>"""

    def get(self, request, slug):
        if slug not in REPORTS:
            raise NotFound("Unknown report.")
        fn, label, _, student_ok = REPORTS[slug]
        if request.user.role == STUDENT and not student_ok:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Students can only export their own attendance and performance reports.")
        report = fn(request.user, request.query_params)
        fmt = request.query_params.get("export", "json")
        if fmt == "json":
            return Response(report)
        if fmt not in ("pdf", "xlsx"):
            raise ValidationError({"export": "Use pdf, xlsx or json."})
        content, ctype, ext = to_pdf(report) if fmt == "pdf" else to_xlsx(report)
        log_action(request, "EXPORT", model_name="Report", changes={"report": slug, "format": fmt})
        resp = HttpResponse(content, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{slug}.{ext}"'
        return resp

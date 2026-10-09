from apps.accounts.audit import log_action

SENSITIVE = {"password", "token"}


def _clean(data):
    out = {}
    for k, v in (data or {}).items():
        if k in SENSITIVE:
            continue
        out[k] = v if isinstance(v, (int, float, bool, type(None))) else str(v)
    return out


class AuditMixin:
    """Writes CREATE / UPDATE / DELETE entries to the audit log."""

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(self.request, "CREATE", instance, _clean(serializer.validated_data))

    def perform_update(self, serializer):
        instance = serializer.save()
        log_action(self.request, "UPDATE", instance, _clean(serializer.validated_data))

    def perform_destroy(self, instance):
        log_action(self.request, "DELETE", instance)
        instance.delete()

from apps.accounts.models import AuditLog


def get_client_ip(request):
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(request, action, instance=None, changes=None, user=None, model_name=None, object_id=None, repr_=None):
    """Create an audit-log entry. Never raises (auditing must not break a request)."""
    try:
        actor = user or (getattr(request, "user", None) if request is not None else None)
        if actor is not None and not getattr(actor, "is_authenticated", False):
            actor = None
        AuditLog.objects.create(
            user=actor,
            action=action,
            model_name=model_name or (instance.__class__.__name__ if instance is not None else ""),
            object_id=str(object_id or (getattr(instance, "pk", "") or "")),
            object_repr=(repr_ or (str(instance) if instance is not None else ""))[:255],
            changes=changes or {},
            ip_address=get_client_ip(request),
        )
    except Exception:  # pragma: no cover
        pass

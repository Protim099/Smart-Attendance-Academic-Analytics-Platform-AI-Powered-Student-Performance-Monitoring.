from .models import Notification


def notify(user, title, message, level="info", link=""):
    return Notification.objects.create(user=user, title=title, message=message, level=level, link=link)


def notify_many(users, title, message, level="info", link=""):
    Notification.objects.bulk_create(
        [Notification(user=u, title=title, message=message, level=level, link=link) for u in users]
    )

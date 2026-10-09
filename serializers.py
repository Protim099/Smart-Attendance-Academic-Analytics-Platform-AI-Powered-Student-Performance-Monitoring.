from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "title", "message", "level", "link", "is_read", "created_at"]
        read_only_fields = ["title", "message", "level", "link", "created_at"]

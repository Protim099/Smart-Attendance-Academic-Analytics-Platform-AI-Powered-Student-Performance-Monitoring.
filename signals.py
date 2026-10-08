from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Result
from .services import recalculate_academic_record


@receiver([post_save, post_delete], sender=Result)
def result_changed(sender, instance, **kwargs):
    recalculate_academic_record(instance.student, instance.exam.course.semester)

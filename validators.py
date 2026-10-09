import os

from django.core.exceptions import ValidationError

DOC_EXT = {".pdf", ".doc", ".docx", ".txt", ".ppt", ".pptx", ".zip", ".png", ".jpg", ".jpeg"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _check(file, allowed, max_mb):
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in allowed:
        raise ValidationError(f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed))}")
    if file.size > max_mb * 1024 * 1024:
        raise ValidationError(f"File too large. Maximum size is {max_mb} MB.")


def validate_document(file):
    _check(file, DOC_EXT, 10)


def validate_image(file):
    _check(file, IMG_EXT, 2)

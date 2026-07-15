"""Валідатори каталогу."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db.models.fields.files import FieldFile

ICON_MAX_BYTES = 64 * 1024
ICON_EXTENSIONS = (".svg", ".png", ".webp")


def validate_icon(value: FieldFile | UploadedFile) -> None:
    """Міні-емблема категорії для мегаменю (INPUTS §4, скрін мегаменю).

    Це FileField, а не ImageField, бо основний формат — SVG (Pillow його не читає, і
    ImageField на ньому падає). Тому перевіряємо розширення й розмір самі.
    """
    name = (getattr(value, "name", "") or "").lower()
    if not name.endswith(ICON_EXTENSIONS):
        raise ValidationError(
            "Емблема має бути %(exts)s.",
            params={"exts": ", ".join(ICON_EXTENSIONS)},
        )
    size = getattr(value, "size", None)
    if size and size > ICON_MAX_BYTES:
        raise ValidationError(
            "Емблема завелика (%(size)d КБ). Максимум — %(max)d КБ: вона вантажиться "
            "в мегаменю для всіх категорій одразу.",
            params={"size": size // 1024, "max": ICON_MAX_BYTES // 1024},
        )

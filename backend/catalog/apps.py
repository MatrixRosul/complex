from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
    verbose_name = "Каталог"

    def ready(self) -> None:
        # Інвалідація Redis-кешу дерева категорій і підписів фасетів.
        # Імпорт саме тут (а не в шапці модуля) — інакше моделі ще не завантажені.
        from catalog import signals  # noqa: F401

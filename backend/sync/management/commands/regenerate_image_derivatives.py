"""Перезібрати WebP-деривативи (lg/md/sm) з оригіналів фото товарів.

Навіщо: до фіксу `sync.tasks.prepare_for_derivatives` деривативи збирались через
`convert("RGB")`, який альфу не композитить, а просто відкидає — і прозорі PNG виїжджали
на сайт із ЧОРНИМ фоном. Фікс коду сам собою НІЧОГО не лікує: у сховищі вже лежать
чорні webp, а дедуп у `download_product_image` іде по `content_hash` вихідних байтів,
тому наступний синк побачить ті самі байти, поверне "unchanged" і нічого не перебудує.

Оригінал (`file`) при цьому не постраждав — там сирі байти з цілою альфою. Тому
перезбираємо саме з нього і рівно тією ж функцією, якою це робить синк
(`sync.tasks.write_derivatives`) — друга копія логіки розʼїхалась би з першою.

    python manage.py regenerate_image_derivatives --only-transparent

⚠️ Мережі тут немає і синк не запускається: команда лише читає й пише сховище.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from django.core.management.base import BaseCommand

from catalog.models import ProductImage
from sync.tasks import has_alpha, write_derivatives

DERIVATIVE_FIELDS = ("file_large", "file_card", "file_thumb")


class Command(BaseCommand):
    help = "Перезібрати WebP-деривативи фото з оригіналів (лікує чорний фон прозорих PNG)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="скільки записів ПЕРЕГЛЯНУТИ (не скільки перезібрати: з --only-transparent "
            "частина переглянутих відсіється)",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="показати, що перезібралося б, без запису"
        )
        parser.add_argument(
            "--only-transparent",
            action="store_true",
            help="лише фото, в оригіналі яких реально є альфа (саме вони й почорніли)",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        from PIL import Image

        qs = ProductImage.objects.exclude(file="").order_by("pk")
        planned = qs.count()
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        self.stdout.write(f"Фото з оригіналом у сховищі: {planned}.")
        rebuilt = skipped = failed = 0

        for index, image in enumerate(qs.iterator(chunk_size=100), start=1):
            label = f"#{image.pk} (товар {image.product_id})"
            try:
                payload = self._read_original(image)
                if payload is None:
                    self.stderr.write(f"  – {label}: оригіналу немає у сховищі")
                    skipped += 1
                    continue

                source_image = Image.open(BytesIO(payload))
                source_image.load()
                alpha = has_alpha(source_image)

                if opts["only_transparent"] and not alpha:
                    skipped += 1
                    continue

                mark = "з альфою" if alpha else "без альфи"
                if opts["dry_run"]:
                    self.stdout.write(f"  DRY  {label} — {source_image.mode}, {mark}")
                    rebuilt += 1
                    continue

                self._rebuild(image, source_image)
                rebuilt += 1
                self.stdout.write(self.style.SUCCESS(f"  ✓ {label} — {source_image.mode}, {mark}"))
            # Один битий файл не має валити прогін на 400 фото: логуємо і йдемо далі.
            except Exception as exc:
                self.stderr.write(f"  ✗ {label}: {exc}")
                failed += 1

            if index % 50 == 0:
                self.stdout.write(f"  … оброблено {index}")

        verb = "Перезібралося б" if opts["dry_run"] else "Перезібрано"
        self.stdout.write(
            self.style.SUCCESS(f"\n{verb}: {rebuilt}. Пропущено: {skipped}. Помилок: {failed}.")
        )

    # --- деталі -------------------------------------------------------------

    @staticmethod
    def _read_original(image: ProductImage) -> bytes | None:
        """Сирі байти оригіналу. None — якщо запис у БД є, а файла у сховищі вже немає."""
        try:
            with image.file.open("rb") as fh:
                return fh.read()
        except (FileNotFoundError, OSError, ValueError):
            return None

    @staticmethod
    def _rebuild(image: ProductImage, source_image: Any) -> None:
        """Перезібрати три деривативи, ЗБЕРІГШИ шлях кожного.

        ⚠️ ЗНОСИМО СТАРЕ ДО ЗАПИСУ, А НЕ ПІСЛЯ. Якщо спершу писати, сховище бачить зайняте
        ім'я і додає суфікс (`..._md.webp` → `..._md_SMcDIEs.webp`) — тоді URL змінюється,
        і кожен, хто вже закешував старий, отримує 404: ISR-кеш Next, CDN, браузер. На проді
        це особливо боляче, бо деривативи віддаються з `Cache-Control: immutable`. До того ж
        суфікси наростали б з кожним прогоном команди.
        Спершу видаливши, ми звільняємо канонічне ім'я — і воно ж дістається новому файлу.
        Ризик втрати при падінні між delete і save мінімальний: оригінал (`file`) цілий,
        достатньо перезапустити команду.
        """
        from django.core.files.storage import default_storage

        for field in DERIVATIVE_FIELDS:
            old_name = getattr(image, field).name or ""
            if not old_name:
                continue
            try:
                default_storage.delete(old_name)
            except Exception as exc:  # осиротілий файл у сховищі — не привід падати
                Command._warn_orphan(old_name, exc)

        stem = image.source_url_hash[:16] or f"img{image.pk}"
        prepared = write_derivatives(image, source_image, stem)
        image.width, image.height = prepared.size
        image.save(update_fields=[*DERIVATIVE_FIELDS, "width", "height", "updated_at"])

    @staticmethod
    def _warn_orphan(name: str, exc: Exception) -> None:
        import logging

        logging.getLogger(__name__).warning("Старий дериватив %s не видалився: %s", name, exc)

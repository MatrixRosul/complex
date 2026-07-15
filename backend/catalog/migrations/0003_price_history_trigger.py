"""ГОТОВА міграція: PostgreSQL-тригер історії цін (ADR-006).

╔══════════════════════════════════════════════════════════════════════════════════════╗
║  ✅ УВЕДЕНО В ДІЮ. Файл приїхав як `0002_price_history_trigger.py.tpl`; міграційний    ║
║  агент виконав усі 4 кроки з його інструкції:                                          ║
║    1) `makemigrations` → catalog/0001_initial.py + 0002_initial.py;                    ║
║    2) в catalog/0001_initial.py дописано ("core", "0001_extensions");                  ║
║    3) файл перейменовано (див. нижче, чому 0003, а не 0002);                           ║
║    4) `migrate` пройшов на чистій PG 14 — тригер у БД.                                 ║
║                                                                                        ║
║  ⚠️ ЯКЩО КОЛИСЬ ДОВЕДЕТЬСЯ ПЕРЕГЕНЕРУВАТИ catalog/0001 З НУЛЯ — спершу ПРИБЕРІТЬ ЦЕЙ    ║
║  ФАЙЛ З migrations/ (у scratchpad, не видаляти). MigrationQuestioner.ask_initial()     ║
║  повертає False, якщо в каталозі лежить будь-який .py, крім __init__.py, і тоді        ║
║  `makemigrations` МОВЧКИ пропустить увесь catalog: рядок «Migrations for 'catalog'»    ║
║  просто не з'явиться, без жодної помилки. Саме на це й був розрахований суфікс .tpl.   ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

ЧОМУ ТРИГЕР, А НЕ PYTHON:
історію цін фізично неможливо вести в Python — її обходять обидва гарячі шляхи:
  • `qs.update(...)` у recalc_prices (зміна курсу/округлення) — один масовий UPDATE;
  • `bulk_create(update_conflicts=True)` у синку.
Тригер покриває їх ОБИДВА плюс ручну правку в адмінці — без жодного шансу забути.
Питання «чому вчора холодильник коштував 27 445, а сьогодні 28 900» тепер має відповідь.

⚠️ РЕБРЕНДИНГ (14.07.2026). Тут імена ВЖЕ нові: `complex_log_price()`, GUC `complex.*`.
   БД, підняті до перейменування, цю міграцію вже застосували зі старими іменами
   (`nisa_log_price()`, `nisa.price_reason`) — їх переводить на нові
   catalog/0004_rename_price_trigger_to_complex. Ім'я тригера `price_history_trg`
   не змінювалось: у ньому немає старої назви проєкту.
"""

from __future__ import annotations

from django.db import migrations

# Контекст задається на початку транзакції прогону:
#     SET LOCAL complex.price_reason = 'sync';
#     SET LOCAL complex.run_id = '<uuid>';
# current_setting(..., true) → NULL замість помилки, якщо їх не виставили (ручна правка).
PRICE_HISTORY_FN = """
CREATE OR REPLACE FUNCTION complex_log_price() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.price IS NOT DISTINCT FROM OLD.price
                        AND NEW.availability = OLD.availability THEN
        RETURN NULL;
    END IF;
    INSERT INTO catalog_pricehistory
        (product_id, price, base_price, source_currency, usd_rate_used,
         markup_percent, availability, reason, run_id, changed_at)
    VALUES (NEW.id, NEW.price, NEW.base_price, NEW.source_currency, NEW.usd_rate_used,
            NEW.markup_percent, NEW.availability,
            COALESCE(current_setting('complex.price_reason', true), 'manual'),
            NULLIF(current_setting('complex.run_id', true), '')::uuid,
            now());
    RETURN NULL;
END $$;
"""

# AFTER INSERT OR UPDATE OF price, availability — тобто UPDATE інших колонок (назва, SEO,
# denorm_dirty) тригер навіть не будить. Плюс ранній RETURN NULL вище: масовий UPDATE, який
# не змінив ані ціну, ані наявність, не пише в журнал жодного рядка.
PRICE_HISTORY_TRG = """
DROP TRIGGER IF EXISTS price_history_trg ON catalog_product;
CREATE TRIGGER price_history_trg
AFTER INSERT OR UPDATE OF price, availability ON catalog_product
FOR EACH ROW EXECUTE FUNCTION complex_log_price();
"""


class Migration(migrations.Migration):
    # 0002 зайняв автодетектор: circular FK catalog ↔ sync/feeds змусив його розбити initial
    # на 0001_initial + 0002_initial (AddField для category.hotline_category,
    # product.price_source, product.winning_offer, pricehistory.run). Тригер вішаємо ПІСЛЯ
    # 0002 — на момент його створення catalog_pricehistory уже має колонку run_id, яку
    # complex_log_price() заповнює з current_setting('complex.run_id').
    dependencies = [
        ("catalog", "0002_initial"),
        ("core", "0001_extensions"),
    ]

    operations = [
        migrations.RunSQL(
            sql=PRICE_HISTORY_FN,
            reverse_sql="DROP FUNCTION IF EXISTS complex_log_price() CASCADE;",
        ),
        migrations.RunSQL(
            sql=PRICE_HISTORY_TRG,
            reverse_sql="DROP TRIGGER IF EXISTS price_history_trg ON catalog_product;",
        ),
    ]

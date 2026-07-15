"""Ребрендинг NISA → Complex: тригерна функція історії цін і змінні сесії (ADR-006).

    nisa_log_price()                    →  complex_log_price()
    SET LOCAL nisa.price_reason / run_id →  complex.price_reason / complex.run_id

Тут, на відміну від core/0003, ПЕРЕЙМЕНУВАННЯМ (`ALTER FUNCTION ... RENAME`) не обійтись:
змінюється не лише ім'я функції, а й ТІЛО — вона читає `current_setting('nisa.price_reason')`,
а `catalog/services/pricing.py::price_reason()` тепер виставляє `complex.price_reason`.
Розійдись вони — журнал цін мовчки писав би 'manual' на КОЖНУ зміну від синку.

Порядок операцій критичний:
  1) CREATE OR REPLACE complex_log_price() — нове тіло з новими GUC;
  2) перевішуємо price_history_trg на неї (ім'я самого тригера НЕ міняємо — у ньому
     немає старого бренду, а зайвий DROP/CREATE тригера — зайвий ACCESS EXCLUSIVE lock);
  3) DROP старої функції — уже без залежностей, тому без CASCADE.
DDL ідемпотентний: на чистій БД (catalog/0003 уже створила complex_log_price) це no-op.
"""

from __future__ import annotations

from django.db import migrations

# Тіло — 1:1 з catalog/0003_price_history_trigger.PRICE_HISTORY_FN.
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

DROP TRIGGER IF EXISTS price_history_trg ON catalog_product;
CREATE TRIGGER price_history_trg
AFTER INSERT OR UPDATE OF price, availability ON catalog_product
FOR EACH ROW EXECUTE FUNCTION complex_log_price();

DROP FUNCTION IF EXISTS nisa_log_price();
"""

REVERSE_FN = """
CREATE OR REPLACE FUNCTION nisa_log_price() RETURNS trigger
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
            COALESCE(current_setting('nisa.price_reason', true), 'manual'),
            NULLIF(current_setting('nisa.run_id', true), '')::uuid,
            now());
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS price_history_trg ON catalog_product;
CREATE TRIGGER price_history_trg
AFTER INSERT OR UPDATE OF price, availability ON catalog_product
FOR EACH ROW EXECUTE FUNCTION nisa_log_price();

DROP FUNCTION IF EXISTS complex_log_price();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_price_history_trigger"),
        ("core", "0003_rename_pg_objects_to_complex"),
    ]

    operations = [
        migrations.RunSQL(sql=PRICE_HISTORY_FN, reverse_sql=REVERSE_FN),
    ]

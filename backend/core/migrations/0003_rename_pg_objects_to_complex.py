"""Ребрендинг NISA → Complex на рівні об'єктів PostgreSQL (SQL-функція + TS-конфіг).

Проєкт перейменовано (14.07.2026), і разом з кодом перейменовано об'єкти БД:
    nisa_price_uah(...)  →  complex_price_uah(...)
    TS-конфіг `ru_nisa`  →  `ru_complex`

⚠️ ЧОМУ ОКРЕМА МІГРАЦІЯ, А НЕ ПРОСТО ПРАВКА 0001_extensions.
`0001_extensions` у вже піднятих БД (dev, staging, прод) ПОЗНАЧЕНА ЯК ЗАСТОСОВАНА і
вдруге не виконається — там досі живуть `nisa_price_uah()` і `ru_nisa`. Тому нові імена
має створити ОКРЕМА міграція. У 0001 імена теж оновлені — для чистої БД, яка ніколи не
бачила старих (там ця міграція просто ідемпотентно підтвердить уже правильний стан).

⚠️ DDL тут НАВМИСНЕ ІДЕМПОТЕНТНИЙ (CREATE OR REPLACE / DO $$ IF NOT EXISTS / DROP IF EXISTS):
одна й та сама міграція мусить відпрацювати ОБИДВА сценарії — і «чиста БД з новими іменами»,
і «стара БД зі старими». Жодних ALTER без перевірки: на чистій БД `ru_nisa` вже не існує.

НЕ ПЕРЕЙМЕНОВАНО СВІДОМО:
  • ім'я БД (`nisa`, тестова — `test_nisa`) — це зламало б локальні оточення й DATABASE_URL;
  • станза pgBackREST (`nisa`) — інша станза = наявний бекап-репозиторій стає невидимим;
  • тригер `price_history_trg` — у назві немає старого бренду, перейменовувати нічого.
"""

from __future__ import annotations

from django.db import migrations

# Тіло — 1:1 з core/0001_extensions.PRICE_FN_SQL (ADR-005). CREATE OR REPLACE:
# на чистій БД функція вже така сама (no-op), на старій — з'являється під новим іменем.
PRICE_FN_SQL = """
CREATE OR REPLACE FUNCTION complex_price_uah(
    base numeric, currency text, rate numeric, markup numeric, rule text
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v numeric;
BEGIN
    IF base IS NULL THEN RETURN NULL; END IF;
    v := base * (1 + COALESCE(markup, 0) / 100.0);
    IF currency = 'USD' THEN
        IF rate IS NULL OR rate <= 0 THEN RETURN NULL; END IF;
        v := v * rate;
    END IF;
    RETURN CASE rule
        WHEN 'none' THEN round(v, 2)
        WHEN 'unit' THEN round(v, 0)
        WHEN 'ten'  THEN round(v / 10, 0) * 10
        WHEN 'nine' THEN CASE WHEN v < 100 THEN ceil(v)
                              ELSE greatest(round(v / 10, 0) * 10 - 1, 9) END
        ELSE round(v, 0)
    END;
END $$;
DROP FUNCTION IF EXISTS nisa_price_uah(numeric, text, numeric, numeric, text);
"""

REVERSE_PRICE_FN_SQL = """
CREATE OR REPLACE FUNCTION nisa_price_uah(
    base numeric, currency text, rate numeric, markup numeric, rule text
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v numeric;
BEGIN
    IF base IS NULL THEN RETURN NULL; END IF;
    v := base * (1 + COALESCE(markup, 0) / 100.0);
    IF currency = 'USD' THEN
        IF rate IS NULL OR rate <= 0 THEN RETURN NULL; END IF;
        v := v * rate;
    END IF;
    RETURN CASE rule
        WHEN 'none' THEN round(v, 2)
        WHEN 'unit' THEN round(v, 0)
        WHEN 'ten'  THEN round(v / 10, 0) * 10
        WHEN 'nine' THEN CASE WHEN v < 100 THEN ceil(v)
                              ELSE greatest(round(v / 10, 0) * 10 - 1, 9) END
        ELSE round(v, 0)
    END;
END $$;
DROP FUNCTION IF EXISTS complex_price_uah(numeric, text, numeric, numeric, text);
"""

# `ru_complex` (а не `ru`): `ru` у частині збірок PG — уже наявний алiас, і CREATE впаде.
# Стара БД → ALTER RENAME (збереже мапінги); чиста → конфіг уже є під новим іменем → no-op.
# Збережені tsvector-и не залежать від імені конфігурації, переіндексація НЕ потрібна.
RU_TS_RENAME_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_complex') THEN
        IF EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_nisa') THEN
            ALTER TEXT SEARCH CONFIGURATION ru_nisa RENAME TO ru_complex;
        ELSE
            CREATE TEXT SEARCH CONFIGURATION ru_complex (COPY = russian);
            ALTER TEXT SEARCH CONFIGURATION ru_complex
                ALTER MAPPING FOR hword, hword_part, word WITH unaccent, russian_stem;
        END IF;
    END IF;
END $$;
DROP TEXT SEARCH CONFIGURATION IF EXISTS ru_nisa;
"""

REVERSE_RU_TS_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_nisa')
       AND EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_complex') THEN
        ALTER TEXT SEARCH CONFIGURATION ru_complex RENAME TO ru_nisa;
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0002_initial")]

    operations = [
        migrations.RunSQL(sql=PRICE_FN_SQL, reverse_sql=REVERSE_PRICE_FN_SQL),
        migrations.RunSQL(sql=RU_TS_RENAME_SQL, reverse_sql=REVERSE_RU_TS_SQL),
    ]

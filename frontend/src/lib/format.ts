import type { Locale } from "@/i18n/config";
import type { SpecRow } from "./api/types";

const LOCALE_TAG: Record<Locale, string> = { uk: "uk-UA", ru: "ru-UA" };

/**
 * Ціна. Без копійок, якщо вони нульові — у побутовій техніці «24 990 ₴»
 * читається краще, ніж «24 990,00 ₴».
 */
export function formatPrice(value: string | number, locale: Locale = "uk"): string {
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (!Number.isFinite(n)) return "—";

  const hasCents = Math.round(n * 100) % 100 !== 0;
  return new Intl.NumberFormat(LOCALE_TAG[locale], {
    minimumFractionDigits: hasCents ? 2 : 0,
    maximumFractionDigits: hasCents ? 2 : 0,
  }).format(n);
}

/** «24 990 ₴» — символ валюти окремо, щоб можна було стилізувати. */
export function formatPriceWithCurrency(value: string | number, locale: Locale = "uk"): string {
  return `${formatPrice(value, locale)} ₴`;
}

/**
 * Абсолютна сума знижки для бейджа: «-3 500 ₴».
 * Замовник і референс (jabko) показують ГРИВНІ, не відсотки — конкретна економія
 * читається сильніше (DESIGN_SYSTEM §1). null, якщо знижки немає.
 */
export function discountAmount(price: string | null, oldPrice: string | null): number | null {
  if (!price || !oldPrice) return null;
  const p = Number.parseFloat(price);
  const o = Number.parseFloat(oldPrice);
  if (!Number.isFinite(p) || !Number.isFinite(o) || o <= p) return null;
  return o - p;
}

/**
 * Відсоток знижки для бейджа: «-12%».
 *
 * ⚠️ ЖОДНОЇ НАКРУЧЕНОЇ МАТЕМАТИКИ. Відсоток рахується від СТАРОЇ ціни (o − p) / o — це
 * єдине чесне означення «знижки»: рахувати від нової ((o − p) / p) дало б «-14%» там,
 * де насправді -12%, тобто просто завищило б цифру на банері.
 *
 * ⚠️ Повертає null, ЯКЩО ЗНИЖКИ НЕМАЄ (немає old_price або old_price ≤ price) — рівно
 * та сама умова, що й у discountAmount(). Бейдж «-0%» не існує.
 *
 * Округлення — Math.round, а не floor/ceil: floor перетворив би реальні -9.9% на «-9%»
 * (обманули себе), ceil — реальні -9.1% на «-10%» (обманули клієнта).
 */
export function discountPercent(price: string | null, oldPrice: string | null): number | null {
  if (!price || !oldPrice) return null;
  const p = Number.parseFloat(price);
  const o = Number.parseFloat(oldPrice);
  if (!Number.isFinite(p) || !Number.isFinite(o) || o <= p || o <= 0) return null;
  const pct = Math.round(((o - p) / o) * 100);
  // Знижка < 0.5% округлилась би в 0 — такий бейдж не показуємо взагалі.
  return pct > 0 ? pct : null;
}

/**
 * ⚠️ ЄДИНЕ місце, де значення характеристики склеюється з одиницею.
 *
 * Жорстка вимога замовника (INPUTS §2):
 *   ПРАВИЛЬНО:   «Висота»       → «284 мм»
 *   НЕПРАВИЛЬНО: «Висота (мм)»  → «284»
 *
 * Одиниця клеїться до ЗНАЧЕННЯ, а не до назви. Ніколи не конкатенуйте row.n + row.u.
 */
export function formatSpecValue(row: Pick<SpecRow, "v" | "u">): string {
  return `${row.v} ${row.u ?? ""}`.trim();
}

/** Назва характеристики — БЕЗ одиниці. Свідомо окрема функція, щоб не було спокуси. */
export function formatSpecName(row: Pick<SpecRow, "n">): string {
  return row.n;
}

/** Групує specs_json у секції («Основні», «Габаритні розміри»), зберігаючи порядок. */
export function groupSpecs(specs: SpecRow[]): { group: string; rows: SpecRow[] }[] {
  const groups = new Map<string, { sort: number; rows: SpecRow[] }>();

  for (const row of specs) {
    const bucket = groups.get(row.g);
    if (bucket) {
      bucket.rows.push(row);
    } else {
      groups.set(row.g, { sort: row.gs, rows: [row] });
    }
  }

  return [...groups.entries()]
    .sort((a, b) => a[1].sort - b[1].sort)
    .map(([group, { rows }]) => ({
      group,
      rows: [...rows].sort((a, b) => a.s - b.s),
    }));
}

export function formatDate(iso: string, locale: Locale = "uk"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE_TAG[locale], {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(d);
}

export function formatDateTime(iso: string, locale: Locale = "uk"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE_TAG[locale], {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

/** Телефон для показу: +380950915222 → +380 95 091 52 22 */
export function formatPhone(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.length !== 12) return raw;
  return `+${digits.slice(0, 3)} ${digits.slice(3, 5)} ${digits.slice(5, 8)} ${digits.slice(8, 10)} ${digits.slice(10)}`;
}

/** Нормалізація телефону перед відправкою на бекенд: усе → +380XXXXXXXXX. */
export function normalizePhone(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.startsWith("380") && digits.length === 12) return `+${digits}`;
  if (digits.startsWith("0") && digits.length === 10) return `+38${digits}`;
  if (digits.length === 9) return `+380${digits}`;
  return raw.trim();
}

export function isValidPhone(raw: string): boolean {
  return /^\+380\d{9}$/.test(normalizePhone(raw));
}

export function isValidEmail(raw: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(raw.trim());
}

/** «61.5 кг · 171,5×56×35 см» — показуємо, що саме пішло в розрахунок доставки. */
export function formatPackage(
  weightKg: string | null,
  h: string | null,
  w: string | null,
  d: string | null,
  locale: Locale = "uk",
): string | null {
  if (!weightKg || !h || !w || !d) return null;
  const fmt = (v: string) =>
    new Intl.NumberFormat(LOCALE_TAG[locale], { maximumFractionDigits: 1 }).format(
      Number.parseFloat(v),
    );
  return `${fmt(weightKg)} кг · ${fmt(h)}×${fmt(w)}×${fmt(d)} см`;
}

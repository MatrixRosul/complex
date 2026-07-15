import type { CategoryOut } from "./api/types";

/**
 * Міні-емблема категорії (мегаменю, головна, бургер) — ключ для <CategoryIcon>.
 *
 * ⚠️ Чому не беремо `category.icon_url` з API. Він є (напр. /media/categories/icons/av.svg),
 * але це РАСТР/SVG-файл із власним кольором: у темній темі він перетворюється на пляму
 * (DESIGN_SYSTEM §4.9 вимагає stroke-емблему з currentColor). Тому іконка — векторна з lucide,
 * а зіставлення йде по slug'у категорії (мовонезалежний, стабільний).
 *
 * ⚠️ Мапа по SLUG, а не по external_id: external_id — це id з Google Sheets, він міняється
 * при перезаливці фіду; slug стабільний і читабельний.
 */
const ICON_BY_SLUG: Record<string, string> = {
  // ── Корені ──
  "audio-video": "tv",
  "vbudovana-tekhnika": "layout-grid",
  "velyka-pobutova-tekhnika": "refrigerator",
  "dribna-pobutova-tekhnika": "coffee",
  "klimatychne-obladnannia": "air-vent",
  "krasa-i-dohliad": "blend",
  posud: "utensils",
  heneratory: "plug",
  utsinka: "package",

  // ── Другий рівень ──
  televizory: "tv",
  audiotekhnika: "speaker",
  "varylni-poverkhni": "flame",
  "dukhovi-shafy": "oven",
  vytiazhky: "fan",
  "vbudovani-kholodylnyky": "refrigerator",
  kholodylnyky: "refrigerator",
  "pralni-mashyny": "washing-machine",
  "posudomyini-mashyny": "utensils",
  "sushylni-mashyny": "shirt",
  "chainyky-elektrychni": "cup-soda",
  "mikrokhvylovi-pechi": "microwave",
  pylososy: "bot",
  kondytsionery: "air-vent",
  obihrivachi: "thermometer-sun",
};

export function categoryIcon(slug: string): string {
  return ICON_BY_SLUG[slug] ?? "package";
}

/** Готова мапа external_id → ключ іконки: серверний компонент передає її клієнтським. */
export function iconMap(categories: CategoryOut[]): Record<string, string> {
  return Object.fromEntries(
    categories.map((c) => [c.external_id, categoryIcon(c.slug)]),
  );
}

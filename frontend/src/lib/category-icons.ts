import type { CategoryOut } from "./api/types";

/**
 * Міні-емблема категорії (мегаменю, головна, бургер) — ключ для <CategoryIcon>.
 *
 * ⚠️ `category.icon_url` ТЕПЕР ВИКОРИСТОВУЄТЬСЯ (раніше був свідомо вимкнений).
 * Причина вимкнення була реальна: файл із власним кольором у темній темі
 * перетворювався на пляму. Її розв'язано не забороною, а способом малювання —
 * див. <CategoryEmblem> у `components/layout/category-icon.tsx`:
 *   • SVG   → малюється через CSS mask-image + background-color: currentColor,
 *             тобто фарбується темою так само, як lucide-іконка;
 *   • РАСТР → звичайний <img> (перефарбувати PNG неможливо, тому він іде як є).
 * Замовниця малює круглі емблеми в колір лого й вантажить їх в адмінку; поки
 * емблеми немає — працює lucide-фолбек нижче.
 *
 * ⚠️ Мапа по SLUG, а не по external_id: external_id — це id з Google Sheets, він міняється
 * при перезаливці фіду; slug стабільний і читабельний.
 */
/**
 * 🔴 МАПА БУЛА ЗАСТАРІЛА — звірено з живим фідом 20.07.2026.
 * Слаги в ній («audio-video», «vbudovana-tekhnika», «krasa-i-dohliad») у БД не існують:
 * фід віддає «audio-video-tekhnika», «vbudovana-pobutova-tekhnika»,
 * «tekhnika-dlia-krasy-i-dohliadu». Через це ТРИ з п'яти коренів малювались generic-
 * коробкою — тим самим «шумом», на який уже скаржились у <CategoryTiles>.
 *
 * Секція «застарілі аліаси» лишена свідомо: каталог живе в Google Sheets, слаги там
 * уже мінялись і можуть повернутись. Зайвий ключ у мапі не коштує нічого, а
 * відсутній — це знову коробка на пів головної.
 */
const ICON_BY_SLUG: Record<string, string> = {
  // ── Корені (фід 20.07.2026) ──
  "audio-video-tekhnika": "tv",
  "vbudovana-pobutova-tekhnika": "layout-grid",
  "velyka-pobutova-tekhnika": "refrigerator",
  "dribna-pobutova-tekhnika": "coffee",
  "tekhnika-dlia-krasy-i-dohliadu": "sparkles",

  // ── Вбудована техніка ──
  "vbudovani-varylni-poverkhni": "flame",
  "vbudovani-vytiazhky": "fan",
  "vbudovani-dukhovi-shafy": "oven",
  "vbudovani-kavomashyny": "coffee",
  "vbudovani-mikrokhvylovi-pechi": "microwave",
  "vbudovani-morozylni-shafy": "snowflake",
  "vbudovani-posudomyini-mashyny": "utensils",
  "vbudovani-kholodylnyky": "refrigerator",
  "kukhonni-myiky-zmishuvachi-ta-aksesuary": "droplets",

  // ── Велика побутова ──
  vytiazhky: "fan",
  "kukhonni-plyty": "cooking-pot",
  "pralni-mashyny": "washing-machine",
  "sushylni-mashyny": "shirt",

  // ── Дрібна побутова ──
  "aksesuary-do-tekhniky": "wrench",
  "hryli-elektrychni": "beef",
  "kavovarky-kavomashyny": "coffee",
  "kukhonni-blendery": "blend",
  "kukhonni-mashyny": "cooking-pot",
  "kukhonni-miksery": "blend",
  "myiky-vysokoho-tysku": "waves",
  "mikrokhvylovi-pechi": "microwave",
  "multyvarky-multypechi-i-frytiurnytsi": "cooking-pot",
  miasorubky: "beef",
  sokovytyskachi: "citrus",
  tostery: "sandwich",
  "pylososy-paroochyshchiuvachi": "bot",
  "utiuhy-parovi-systemy": "shirt",
  "chainyky-elektrychni": "cup-soda",
  televizory: "tv",

  // ── Третій рівень (у меню не показується, але лічильники/крихти його чіпають) ──
  "hazovi-varylni-poverkhni": "flame",
  "elektrychni-varylni-poverkhni": "flame",
  "induktsiini-varylni-poverkhni": "flame",
  "varylni-poverkhni-domino": "flame",
  "kombinovani-varylni-poverkhni": "flame",
  "zmishuvachi-kukhonni": "droplets",
  "kukhonni-myiky": "droplets",
  elektroplyty: "cooking-pot",
  "kombinovani-plyty": "cooking-pot",

  // ── Застарілі аліаси з попередніх версій фіду ──
  "audio-video": "tv",
  "vbudovana-tekhnika": "layout-grid",
  "klimatychne-obladnannia": "air-vent",
  "krasa-i-dohliad": "sparkles",
  posud: "utensils",
  heneratory: "plug",
  utsinka: "package",
  audiotekhnika: "speaker",
  "varylni-poverkhni": "flame",
  "dukhovi-shafy": "oven",
  kholodylnyky: "refrigerator",
  "posudomyini-mashyny": "utensils",
  pylososy: "bot",
  kondytsionery: "air-vent",
  obihrivachi: "thermometer-sun",
};

export function categoryIcon(slug: string): string {
  return ICON_BY_SLUG[slug] ?? "package";
}

/** Що малювати замість категорії: завантажена емблема або lucide-фолбек. */
export type CategoryEmblemData = {
  /** Ключ lucide-іконки — використовується, якщо `url` порожній. */
  icon: string;
  /** `icon_url` з адмінки. null → малюємо lucide. */
  url: string | null;
};

/**
 * Готова мапа external_id → емблема: серверний компонент рахує її один раз
 * і передає клієнтським (мегаменю, бургер, сайдбар головної), щоб ті не тягли
 * за собою ні дерево категорій, ні мапінг слагів.
 */
export function emblemMap(categories: CategoryOut[]): Record<string, CategoryEmblemData> {
  return Object.fromEntries(
    categories.map((c) => [c.external_id, { icon: categoryIcon(c.slug), url: c.icon_url }]),
  );
}

/**
 * WIRE-типи: те, що РЕАЛЬНО віддає Django Ninja (/api/v1/openapi.json).
 *
 * Свідомо окремо від `types.ts` (доменні типи, які споживають 53 компоненти).
 * Дві форми не збігаються в одному місці — і саме там ховається помилка, яку
 * TypeScript не спіймає, бо `any` з fetch() підходить під будь-що:
 *
 *   API:    specs: [{ group, group_sort, rows: [{ code, name, value, unit, display, vn }] }]
 *   фронт:  specs: [{ g, gs, code, n, u, v, vn, s }]                    (плоский список)
 *
 * Міст між ними — рівно один: адаптери в `http.ts`. Якщо бекенд змінить форму,
 * зламається саме тут, у типах, а не мовчазним `undefined` у розмітці картки.
 */

export type WireBrand = {
  id: number;
  name: string;
  slug: string;
  logo_url: string | null;
};

export type WireCountry = {
  id: number;
  code: string;
  name: string;
  slug: string;
};

export type WireBreadcrumb = {
  id: number;
  external_id: string;
  name: string;
  slug: string;
};

export type WireCategoryTree = {
  id: number;
  external_id: string;
  name: string;
  slug: string;
  parent_id: number | null;
  depth: number;
  products_count: number;
  icon_url: string | null;
  image_url: string | null;
  children: WireCategoryTree[];
};

export type WireImage = {
  id: number;
  url: string;
  alt: string;
  width: number | null;
  height: number | null;
  sort_order: number;
  is_main: boolean;
};

export type WireSwatch = { hex: string; product_id: number };

export type WireProductListItem = {
  id: number;
  sku: string;
  name: string;
  slug: string;
  /** ⚠️ Гроші — РЯДКОМ ("27449.00"), не number: 0.1+0.2 у JS = 0.30000000000000004. */
  price: string;
  old_price: string | null;
  availability: string;
  order_lead_days: number | null;
  condition: number;
  is_featured: boolean;
  installment_available: boolean;
  installment_max_payments: number | null;
  main_image_url: string | null;
  brand: WireBrand | null;
  swatches: WireSwatch[];
};

/** ⚠️ `display` — ГОТОВА права колонка («284 мм»). Одиниця вже приклеєна до ЗНАЧЕННЯ. */
export type WireSpecRow = {
  code: string;
  /** Назва БЕЗ одиниці: «Висота», а не «Висота (мм)». */
  name: string;
  value: string;
  unit: string;
  display: string;
  vn: number | null;
};

export type WireSpecGroup = {
  group: string;
  group_sort: number;
  rows: WireSpecRow[];
};

export type WireVariantItem = {
  product_id: number;
  label: string;
  swatch_hex: string | null;
  slug: string;
  is_active: boolean;
  availability: string;
  is_current: boolean;
};

export type WireVariantGroup = {
  id: number;
  axis_label: string;
  widget: string;
  items: WireVariantItem[];
};

export type WireProductDetail = WireProductListItem & {
  description: string;
  short_description: string;
  mpn: string;
  barcode: string;
  condition_note: string;
  stock_qty: number | null;
  warranty_months: number | null;
  category: WireBreadcrumb | null;
  breadcrumbs: WireBreadcrumb[];
  country: WireCountry | null;
  images: WireImage[];
  specs: WireSpecGroup[];
  variant_groups: WireVariantGroup[];
  related: WireProductListItem[];
  package_weight_kg: string | null;
  package_width_cm: string | null;
  package_height_cm: string | null;
  package_depth_cm: string | null;
  seo_title: string;
  seo_description: string;
};

export type WireFacetValue = { value: string; label: string; count: number };

export type WireFacetGroup = {
  code: string;
  label: string;
  widget: string;
  unit: string;
  /** «Основні», «Функції та можливості» — секція, до якої належить фасет. */
  group: string;
  values: WireFacetValue[];
};

export type WirePriceRange = { min: number; max: number };

export type WireCatalog = {
  items: WireProductListItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  facets: WireFacetGroup[];
  price_range: WirePriceRange;
  category: WireCategoryTree | null;
  breadcrumbs: WireBreadcrumb[];
  subcategories: WireCategoryTree[];
};

/** /search — той самий лістинг, але без категорії/крихт/підкатегорій. */
export type WireSearch = {
  items: WireProductListItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  query: string;
  facets: WireFacetGroup[];
  price_range: WirePriceRange | null;
};

/** Секція головної, зібрана по КАТЕГОРІЇ (а не по прапорцю товару). */
export type WireCategorySection = {
  id: number;
  name: string;
  slug: string;
  products: WireProductListItem[];
};

/** GET /products/collections — добірки для головної (catalog/schemas.py::CollectionsOut). */
export type WireCollections = {
  featured: WireProductListItem[];
  sale: WireProductListItem[];
  new: WireProductListItem[];
  clearance: WireProductListItem[];
  /**
   * ⚠️ Головна не може спиратися ЛИШЕ на featured/sale/clearance: усі три збираються
   * за полями, які заповнює замовник (is_featured, old_price, condition), і зараз вони
   * порожні в усіх 314 товарів. Секції по категоріях наповнені завжди, бо тримаються
   * на самому каталозі — саме вони і несуть головну.
   */
  by_category: WireCategorySection[];
};

/** GET /brands — топ брендів за кількістю активних товарів. */
export type WireBrandListItem = WireBrand & { products_count: number };

/** Рядок /products/bulk = картка товару + кількість (BulkLineOut успадковує ProductListItemOut). */
export type WireBulkLine = WireProductListItem & {
  qty: number;
  line_total: string;
  is_active: boolean;
};

export type WireBulk = {
  items: WireBulkLine[];
  subtotal: string;
  /** Одне значення під двома ключами (назва з ТЗ + назва, яку чекав фронт). */
  installment_available_for_all: boolean;
  installment_allowed: boolean;
  changed_items: number[];
  unavailable_items: number[];
};

export type WireBanner = {
  id: number;
  title: string;
  subtitle: string;
  image_url: string;
  mobile_image_url: string | null;
  link: string;
  cta_label: string;
  placement: string;
  focal_point: string;
};

export type WireNewsPost = {
  id: number;
  title: string;
  slug: string;
  excerpt: string;
  body: string;
  cover_url: string | null;
  published_at: string;
};

export type WireStaticPage = {
  id: number;
  title: string;
  slug: string;
  body: string;
  updated_at: string;
};

export type WireMenuItem = {
  id: number;
  title: string;
  url: string;
  block: string;
};

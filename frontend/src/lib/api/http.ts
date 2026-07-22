import type { ComplexApi } from "./contract";
import { mediaUrl } from "@/lib/media";
import { FALLBACK_CONTACTS, PAGE_MENU_BLOCK } from "@/lib/site";
import {
  ApiError,
  type ApiLang,
  type Availability,
  type BannerOut,
  type BrandOut,
  type CartPreviewItem,
  type CartPreviewRequestItem,
  type CartPreviewResponse,
  type CatalogQuery,
  type CatalogResponse,
  type CategoryOut,
  type Condition,
  type ContactsOut,
  type DeliveryQuoteOut,
  type FacetGroup,
  type FacetWidget,
  type ImageOut,
  type MenuItemOut,
  type NewsPostOut,
  type NPCityOut,
  type NPWarehouseOut,
  type OrderOut,
  type ProductDetail,
  type ProductListItem,
  type SpecRow,
  type StaticPageOut,
  type VariantGroupOut,
} from "./types";
import type {
  WireBanner,
  WireBulk,
  WireBulkLine,
  WireCatalog,
  WireBrandListItem,
  WireCategoryTree,
  WireCollections,
  WireFacetGroup,
  WireMenuItem,
  WireNewsPost,
  WireProductDetail,
  WireProductListItem,
  WireSearch,
  WireStaticPage,
} from "./wire";

/**
 * Реалізація контракту поверх реального Django Ninja (/api/v1).
 *
 * ⚠️ ЩО ТУТ НЕТРИВІАЛЬНОГО — три місця, де wire-форма ≠ доменна форма:
 *
 *   1. SPECS. API: [{group, group_sort, rows:[{code,name,value,unit,display,vn}]}]
 *      Фронт: плоский SpecRow[] {g,gs,code,n,u,v,vn,s}. Розкладаємо в `adaptSpecs()`.
 *      Одиниця лишається В ОКРЕМОМУ полі `u` — клеїть її до ЗНАЧЕННЯ formatSpecValue(),
 *      і результат збігається з готовим `display` з бекенда (INPUTS §2).
 *
 *   2. ЛІСТИНГ. Категорія → GET /catalog/{ref}; пошук без категорії → GET /search
 *      (SearchOut не має category/breadcrumbs/subcategories — доповнюємо null/[]).
 *
 *   3. МЕДІА. Django віддає /media/… відносним шляхом, а фронт живе на іншому порту —
 *      без mediaUrl() банери й іконки категорій ішли б на localhost:3001 і давали 404.
 *
 * ⚠️ ЩО ЩЕ НЕ НАПИСАНЕ НА БЕКЕНДІ (перевірено curl'ом, не здогадка):
 *      POST /orders, GET /orders/{token}  → 404
 *      /delivery/cities|warehouses|quote  → 404
 *      GET /cms/contacts                  → 404
 *   Ці п'ять методів делеговані в mock — інакше checkout і калькулятор доставки
 *   впали б у 404 замість того, щоб працювати. Контакти — статична константа з INPUTS §1
 *   (реальні телефони замовника), а не вигадані дані. Коли роутери з'являться —
 *   міняється рівно цей файл, жодного компонента.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001/api/v1";

/** ISR: ADR-023 — on-demand revalidateTag ПЛЮС time-based fallback (самозагоєння). */
const TTL = {
  tree: 3600,
  catalog: 600,
  product: 600,
  cms: 600,
  pages: 3600,
} as const;

type FetchOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  /** Теги ISR: `catalog:ru`, `product:230:uk` — мова ЗАВЖДИ в ключі (ARCHITECTURE §2.2). */
  tags?: string[];
  revalidate?: number | false;
  /** 404 → null замість кидання (для сторінок, що самі рендерять notFound()). */
  nullOn404?: boolean;
};

async function request<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { method = "GET", body, tags, revalidate, nullOn404 } = options;

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      next: method === "GET" && (tags || revalidate !== undefined) ? { tags, revalidate } : undefined,
      // Кошик/замовлення НІКОЛИ не кешуємо — це і є захист від протухлих цін.
      cache: method === "POST" ? "no-store" : undefined,
    });
  } catch (cause) {
    // Бекенд лежить / DNS / таймаут. Кидаємо ApiError, а не голий TypeError:
    // сторінка має показати «сервіс недоступний», а не білий екран з fetch failed.
    throw new ApiError(0, `${method} ${path} → недоступний`, cause);
  }

  if (res.status === 404 && nullOn404) return null as T;

  if (!res.ok) {
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      payload = await res.text().catch(() => undefined);
    }
    throw new ApiError(res.status, `${method} ${path} → ${res.status}`, payload);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ─────────────────────────────────────────────────────────────────────────────
// Адаптери wire → домен
// ─────────────────────────────────────────────────────────────────────────────

const asAvailability = (value: string): Availability =>
  value === "in_stock" || value === "on_order" || value === "out_of_stock"
    ? value
    : "out_of_stock";

const asCondition = (value: number): Condition =>
  value === 0 || value === 1 || value === 2 || value === 3 ? value : 0;

const asFacetWidget = (value: string): FacetWidget =>
  value === "switch" || value === "range_slider" ? value : "checkbox";

function adaptBrand(brand: WireProductListItem["brand"]): BrandOut | null {
  if (!brand) return null;
  return { ...brand, logo_url: mediaUrl(brand.logo_url) };
}

function adaptListItem(p: WireProductListItem): ProductListItem {
  return {
    id: p.id,
    sku: p.sku,
    name: p.name,
    slug: p.slug,
    price: p.price,
    old_price: p.old_price,
    availability: asAvailability(p.availability),
    order_lead_days: p.order_lead_days,
    condition: asCondition(p.condition),
    is_featured: p.is_featured,
    installment_available: p.installment_available,
    installment_max_payments: p.installment_max_payments,
    main_image_url: mediaUrl(p.main_image_url),
    brand: adaptBrand(p.brand),
    swatches: p.swatches,
  };
}

/**
 * Групи характеристик → плоский список, який чекають SpecsTable і CompareTable.
 *
 * ⚠️ `u` лишається ОКРЕМИМ полем. Спокуса «взяти готовий display і покласти у v»
 * зламала б порівняння: там значення порівнюються між товарами, і «284 мм» проти
 * «284 мм » (пробіл) — це вже різні рядки. Клеїть одиницю formatSpecValue(), одне місце.
 */
function adaptSpecs(groups: WireProductDetail["specs"]): SpecRow[] {
  const rows: SpecRow[] = [];

  for (const group of groups) {
    group.rows.forEach((row, index) => {
      rows.push({
        g: group.group,
        gs: group.group_sort,
        code: row.code,
        n: row.name,
        u: row.unit,
        v: row.value,
        vn: row.vn,
        s: index,
      });
    });
  }

  return rows;
}

function adaptImages(images: WireProductDetail["images"], name: string): ImageOut[] {
  return images
    .map((img) => ({
      id: img.id,
      url: mediaUrl(img.url) ?? "",
      alt: img.alt || name,
      width: img.width,
      height: img.height,
      sort_order: img.sort_order,
      is_main: img.is_main,
    }))
    .filter((img) => img.url.length > 0);
}

function adaptVariantGroups(groups: WireProductDetail["variant_groups"]): VariantGroupOut[] {
  return groups.map((group) => ({
    id: group.id,
    axis_label: group.axis_label,
    // Кнопки діагоналей vs кружечки кольорів — вісь БУДЬ-ЯКА характеристика (INPUTS §4).
    widget: group.widget === "swatches" ? "swatches" : "buttons",
    items: group.items.map((item) => ({
      product_id: item.product_id,
      label: item.label,
      swatch_hex: item.swatch_hex,
      slug: item.slug,
      is_active: item.is_active,
      availability: asAvailability(item.availability),
      is_current: item.is_current,
    })),
  }));
}

function adaptDetail(p: WireProductDetail): ProductDetail {
  const category = p.category ?? p.breadcrumbs.at(-1) ?? null;

  return {
    ...adaptListItem(p),
    description: p.description,
    short_description: p.short_description,
    mpn: p.mpn,
    barcode: p.barcode,
    category: category ?? { id: 0, external_id: "", name: "", slug: "" },
    breadcrumbs: p.breadcrumbs,
    country: p.country,
    warranty_months: p.warranty_months,
    condition_note: p.condition_note,
    stock_qty: p.stock_qty,
    images: adaptImages(p.images, p.name),
    specs: adaptSpecs(p.specs),
    variant_groups: adaptVariantGroups(p.variant_groups),
    package_weight_kg: p.package_weight_kg,
    package_width_cm: p.package_width_cm,
    package_height_cm: p.package_height_cm,
    package_depth_cm: p.package_depth_cm,
    seo_title: p.seo_title,
    seo_description: p.seo_description,
  };
}

function adaptCategory(c: WireCategoryTree): CategoryOut {
  return {
    id: c.id,
    external_id: c.external_id,
    name: c.name,
    slug: c.slug,
    parent_id: c.parent_id,
    depth: c.depth,
    products_count: c.products_count,
    icon_url: mediaUrl(c.icon_url),
    image_url: mediaUrl(c.image_url),
    children: (c.children ?? []).map(adaptCategory),
  };
}

function adaptFacets(facets: WireFacetGroup[]): FacetGroup[] {
  return facets.map((f) => ({
    code: f.code,
    label: f.label,
    widget: asFacetWidget(f.widget),
    unit: f.unit,
    group: f.group || undefined,
    values: f.values,
  }));
}

function adaptCatalog(data: WireCatalog): CatalogResponse {
  return {
    items: data.items.map(adaptListItem),
    total: data.total,
    page: data.page,
    page_size: data.page_size,
    pages: data.pages,
    facets: adaptFacets(data.facets),
    price_range: data.price_range,
    category: data.category ? adaptCategory(data.category) : null,
    breadcrumbs: data.breadcrumbs,
    subcategories: (data.subcategories ?? []).map(adaptCategory),
  };
}

/** /search віддає SearchOut — той самий лістинг без категорії/крихт/підкатегорій. */
function adaptSearch(data: WireSearch): CatalogResponse {
  return {
    items: data.items.map(adaptListItem),
    total: data.total,
    page: data.page,
    page_size: data.page_size,
    pages: data.pages,
    facets: adaptFacets(data.facets ?? []),
    price_range: data.price_range ?? { min: 0, max: 0 },
    category: null,
    breadcrumbs: [],
    subcategories: [],
  };
}

function adaptBulkLine(line: WireBulkLine): CartPreviewItem {
  return {
    id: line.id,
    qty: line.qty,
    name: line.name,
    slug: line.slug,
    sku: line.sku,
    price: line.price,
    old_price: line.old_price,
    line_total: line.line_total,
    availability: asAvailability(line.availability),
    installment_available: line.installment_available,
    installment_max_payments: line.installment_max_payments,
    is_active: line.is_active,
    main_image_url: mediaUrl(line.main_image_url),
  };
}

function adaptBanner(b: WireBanner): BannerOut {
  return {
    id: b.id,
    title: b.title,
    subtitle: b.subtitle,
    image_url: mediaUrl(b.image_url) ?? "",
    mobile_image_url: mediaUrl(b.mobile_image_url),
    link: b.link,
    cta_label: b.cta_label,
    placement: b.placement,
    focal_point: b.focal_point,
  };
}

function adaptNews(n: WireNewsPost): NewsPostOut {
  return { ...n, cover_url: mediaUrl(n.cover_url) };
}

// ─────────────────────────────────────────────────────────────────────────────
// Query-string
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Розкладає CatalogQuery у query-string Django Ninja.
 *
 * ⚠️ Фасети йдуть ПОВТОРЮВАНИМИ ключами: ?brand=bosch&brand=gorenje.
 * `params.append`, а не `set` — інакше з двох брендів вижив би один, і мультивибір
 * мовчки перетворився б на одиночний (бекенд читає request.GET.lists()).
 */
function buildCatalogSearch(query: CatalogQuery, lang: ApiLang): string {
  const params = new URLSearchParams();
  params.set("lang", lang);

  if (query.q) params.set("q", query.q);
  if (query.page) params.set("page", String(query.page));
  if (query.page_size) params.set("page_size", String(query.page_size));
  if (query.sort) params.set("sort", query.sort);
  if (query.price_min !== undefined) params.set("price_min", String(query.price_min));
  if (query.price_max !== undefined) params.set("price_max", String(query.price_max));

  for (const [code, values] of Object.entries(query.facets ?? {})) {
    for (const value of values) params.append(code, value);
  }

  return params.toString();
}

// ─────────────────────────────────────────────────────────────────────────────
// Реалізація
// ─────────────────────────────────────────────────────────────────────────────

export const httpApi: ComplexApi = {
  async getCategoryTree(lang) {
    const tree = await request<WireCategoryTree[]>(`/categories/tree?lang=${lang}`, {
      tags: [`categories:${lang}`],
      revalidate: TTL.tree,
    });
    return tree.map(adaptCategory);
  },

  async getCatalog(query, lang) {
    const qs = buildCatalogSearch(query, lang);

    // Категорія → лістинг категорії (з крихтами й підкатегоріями).
    if (query.category) {
      const data = await request<WireCatalog>(
        `/catalog/${encodeURIComponent(query.category)}?${qs}`,
        {
          tags: [`catalog:${lang}`, `category:${query.category}:${lang}`],
          revalidate: TTL.catalog,
        },
      );
      return adaptCatalog(data);
    }

    // Пошук без категорії → FTS + trigram-фолбек. Фасети тут теж працюють.
    if (query.q) {
      const data = await request<WireSearch>(`/search?${qs}`, {
        tags: [`catalog:${lang}`],
        revalidate: TTL.catalog,
      });
      return adaptSearch(data);
    }

    // Ні категорії, ні запиту — увесь каталог (сумісний шлях бекенда).
    const data = await request<WireCatalog>(`/catalog/products?${qs}`, {
      tags: [`catalog:${lang}`],
      revalidate: TTL.catalog,
    });
    return adaptCatalog(data);
  },

  async getProduct(id, lang) {
    const data = await request<WireProductDetail | null>(`/products/${id}?lang=${lang}`, {
      tags: [`product:${id}:${lang}`, `catalog:${lang}`],
      revalidate: TTL.product,
      nullOn404: true,
    });
    return data ? adaptDetail(data) : null;
  },

  async getRelatedProducts(id, lang) {
    const items = await request<WireProductListItem[]>(`/products/${id}/related?lang=${lang}`, {
      tags: [`product:${id}:${lang}`],
      revalidate: TTL.product,
    });
    return items.map(adaptListItem);
  },

  // ⚠️ Без кешу (POST → no-store): у бажаннях ціна мусить бути свіжою.
  async getProductsBulk(ids, lang) {
    if (ids.length === 0) return [];

    const data = await request<WireBulk>(`/products/bulk?lang=${lang}`, {
      method: "POST",
      body: ids.map((id) => ({ id, qty: 1 })),
    });
    // Успадкування BulkLineOut від ProductListItemOut дає повну картку:
    // свотчі, condition, order_lead_days — усе, що рендерить ProductCard.
    return data.items.map(adaptListItem);
  },

  // Порівнянню потрібні specs, яких у bulk немає. Максимум 8 товарів → максимум 8 запитів.
  async getProductDetails(ids, lang) {
    if (ids.length === 0) return [];

    const results = await Promise.all(ids.map((id) => httpApi.getProduct(id, lang)));
    return results.filter((p): p is ProductDetail => p !== null);
  },

  // ⚠️ ЄДИНЕ джерело цін кошика. Ніякого кешу — інакше сенс ендпоінта втрачається.
  async previewCart(items: CartPreviewRequestItem[], lang): Promise<CartPreviewResponse> {
    const data = await request<WireBulk>(`/products/bulk?lang=${lang}`, {
      method: "POST",
      body: items,
    });

    return {
      items: data.items.map(adaptBulkLine),
      subtotal: data.subtotal,
      // Бекенд віддає одне значення під двома ключами; беремо те, що з ТЗ, з фолбеком.
      installment_allowed: data.installment_available_for_all ?? data.installment_allowed,
      changed_items: data.changed_items,
      unavailable_items: data.unavailable_items,
    };
  },

  async getBanners(lang): Promise<BannerOut[]> {
    const banners = await request<WireBanner[]>(`/cms/banners?lang=${lang}`, {
      tags: [`banners:${lang}`],
      revalidate: TTL.cms,
    });
    return banners.map(adaptBanner);
  },

  async getCollections(lang) {
    // Один запит замість двох (featured + discounted) — і бекенд віддає його з Redis-кешу.
    const data = await request<WireCollections>(`/products/collections?lang=${lang}`, {
      tags: [`catalog:${lang}`],
      revalidate: TTL.cms,
    });
    return {
      featured: data.featured.map(adaptListItem),
      sale: data.sale.map(adaptListItem),
      new: data.new.map(adaptListItem),
      clearance: data.clearance.map(adaptListItem),
      // Бекенд може ще не знати про by_category (старіший образ) — тоді просто немає
      // секцій по категоріях, а не падіння головної на .map() по undefined.
      by_category: (data.by_category ?? []).map((section) => ({
        id: section.id,
        name: section.name,
        slug: section.slug,
        products: section.products.map(adaptListItem),
      })),
    };
  },

  async getBrands(lang, limit = 12) {
    const items = await request<WireBrandListItem[]>(
      `/brands?lang=${lang}&limit=${limit}`,
      { tags: [`catalog:${lang}`], revalidate: TTL.cms },
    );
    return items.map((b) => ({
      id: b.id,
      name: b.name,
      slug: b.slug,
      logo_url: b.logo_url,
      products_count: b.products_count,
    }));
  },

  async getFeaturedProducts(lang) {
    const items = await request<WireProductListItem[]>(`/products/featured?lang=${lang}`, {
      tags: [`catalog:${lang}`],
      revalidate: TTL.cms,
    });
    return items.map(adaptListItem);
  },

  async getDiscountedProducts(lang) {
    const items = await request<WireProductListItem[]>(`/products/discounted?lang=${lang}`, {
      tags: [`catalog:${lang}`],
      revalidate: TTL.cms,
    });
    return items.map(adaptListItem);
  },

  async getNews(lang): Promise<NewsPostOut[]> {
    const posts = await request<WireNewsPost[]>(`/cms/news?lang=${lang}`, {
      tags: [`news:${lang}`],
      revalidate: TTL.cms,
    });
    return posts.map(adaptNews);
  },

  async getNewsPost(slug, lang) {
    const post = await request<WireNewsPost | null>(`/cms/news/${slug}?lang=${lang}`, {
      tags: [`news:${lang}`],
      revalidate: TTL.cms,
      nullOn404: true,
    });
    return post ? adaptNews(post) : null;
  },

  getStaticPage: (slug, lang) =>
    request<StaticPageOut | null>(`/cms/pages/${slug}?lang=${lang}`, {
      tags: [`pages:${lang}`],
      revalidate: TTL.pages,
      nullOn404: true,
    }),

  /**
   * Меню бургера й футера.
   *
   * ⚠️ MenuItem у БД зараз ПОРОЖНІЙ (GET /cms/menu → []). Порожнє меню = футер без
   * жодного посилання й бургер без «Доставка й оплата» — виглядає як зламаний сайт.
   * Тому фолбек: будуємо меню зі СПИСКУ СТАТИЧНИХ СТОРІНОК (/cms/pages), які в БД є.
   * Щойно контент-менеджер заведе MenuItem — використається він, фолбек вимкнеться сам.
   */
  async getMenuItems(lang): Promise<MenuItemOut[]> {
    const items = await request<WireMenuItem[]>(`/cms/menu?lang=${lang}`, {
      tags: [`menu:${lang}`],
      revalidate: TTL.pages,
    });

    if (items.length > 0) return items;

    const pages = await request<WireStaticPage[]>(`/cms/pages?lang=${lang}`, {
      tags: [`pages:${lang}`],
      revalidate: TTL.pages,
    });

    return pages.map((page) => ({
      id: page.id,
      title: page.title,
      url: `/page/${page.slug}`,
      block: PAGE_MENU_BLOCK[page.slug] ?? "info",
    }));
  },

  /**
   * Контакти. GET /cms/contacts на бекенді ЩЕ НЕМАЄ (404) — віддаємо константу з INPUTS §1
   * (реальні телефони й адреса замовника). Хедер і футер не мають права падати через це.
   */
  async getContacts(lang): Promise<ContactsOut> {
    try {
      return await request<ContactsOut>(`/cms/contacts?lang=${lang}`, {
        tags: [`contacts:${lang}`],
        revalidate: TTL.pages,
      });
    } catch {
      return FALLBACK_CONTACTS(lang);
    }
  },

  /**
   * ── ЗАМОВЛЕННЯ ──────────────────────────────────────────────────────────
   *
   * Роутер на бекенді з'явився (`orders/api.py`, змонтований у config/api.py), тому
   * заглушки 501 більше немає — ходимо в реальне API.
   *
   * ⚠️ ІСТОРІЯ, ЯКУ НЕ ТРЕБА ПОВТОРЮВАТИ: колись ці методи делегувались у mock, і це
   * було ГІРШЕ за чесну помилку. `createOrder` виконувався в БРАУЗЕРІ, складаючи
   * замовлення в in-memory Map вкладки, а `/order/[token]` — Server Component — читав
   * іншу Map в іншому процесі. Підсумок: кошик очищався, людину редіректило на
   * сторінку замовлення, а там «Замовлення не знайдено». Ніколи не повертайте сюди
   * mock: краще 5xx, ніж фейкове «успішно оформлено».
   *
   * ⚠️ 409 обробляє форма (`checkout-form.tsx`): ціни змінились між preview і сабмітом.
   */
  async createOrder(payload, lang): Promise<OrderOut> {
    // ⚠️ body — ОБ'ЄКТ, не рядок: JSON.stringify робить сам `request`.
    //    POST там-таки примусово йде з cache: "no-store".
    return request<OrderOut>(`/orders?lang=${lang}`, { method: "POST", body: payload });
  },

  /** Сторінка замовлення має показати «не знайдено», а не впасти в error boundary. */
  async getOrderByToken(token): Promise<OrderOut | null> {
    // revalidate: 0 — статус оплати й ТТН міняються вебхуком і менеджером,
    // закешована сторінка замовлення показувала б «очікує оплати» після оплати.
    return request<OrderOut | null>(`/orders/${token}`, { revalidate: 0, nullOn404: true });
  },

  /**
   * ── ДОСТАВКА: РОУТЕР Є, І ЗАВЖДИ БУВ ────────────────────────────────────
   *
   * ⚠️ Коментар, що стояв тут раніше («роутерів теж немає»), був ПРОСТО НЕПРАВДОЮ, і саме
   * він законсервував баг: усі три методи безумовно делегувались у mock, тому калькулятор
   * показував 7 ВИГАДАНИХ міст (mock/data.ts) — незалежно від того, що лежить у БД.
   * Насправді `config/api.py` реєструє `/delivery/*`, і ендпоінти віддають реальні дані:
   *     GET  /delivery/settlements?q=…   → довідник НП з нашої БД (trigram, <5 мс)
   *     GET  /delivery/warehouses?…      → відділення/поштомати, з відсіюванням за габаритами
   *     POST /delivery/quote             → вартість (ЗАВЖДИ 200; при збої НП — ok:false)
   *
   * Наслідок бага був відкладений і тому підступний: коли замовник дасть NP_API_KEY і
   * `sync_np_refs` наллє тисячі міст — покупець НЕ ПОБАЧИВ БИ ЖОДНОГО з них, бо фронт
   * узагалі не питав бекенд. Тепер питає.
   */
  async searchCities(query) {
    if (query.trim().length < 2) return [];
    return request<NPCityOut[]>(
      `/delivery/settlements?q=${encodeURIComponent(query)}&limit=10`,
      { revalidate: TTL.pages },
    );
  },

  async getWarehouses(settlementRef) {
    if (!settlementRef) return [];
    return request<NPWarehouseOut[]>(
      `/delivery/warehouses?settlement=${encodeURIComponent(settlementRef)}`,
      { revalidate: TTL.pages },
    );
  },

  /** POST → без кешу. Ціна доставки залежить від тарифів НП, а не від нашого ISR. */
  async quoteDelivery(payload) {
    return request<DeliveryQuoteOut>("/delivery/quote", { method: "POST", body: payload });
  },
};

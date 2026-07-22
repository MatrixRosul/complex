import type { ComplexApi } from "../contract";
import type {
  ApiLang,
  BannerOut,
  CartPreviewItem,
  CartPreviewResponse,
  CatalogQuery,
  CatalogResponse,
  CategoryOut,
  ContactsOut,
  CreateOrderRequest,
  DeliveryQuoteOut,
  DeliveryQuoteRequest,
  FacetGroup,
  FacetValue,
  MenuItemOut,
  NewsPostOut,
  NPCityOut,
  NPWarehouseOut,
  OrderOut,
  ProductDetail,
  ProductListItem,
  SortKey,
  StaticPageOut,
  VariantGroupOut,
} from "../types";
import {
  attr,
  attributes,
  brands,
  buildCategoryTree,
  buildSpecs,
  buildTokens,
  categoryBreadcrumbs,
  categoryWithDescendants,
  contacts,
  country,
  findCategory,
  findCategoryBySlugPath,
  npCities,
  npWarehouses,
  productImages,
  productsCountIn,
  rawBanners,
  rawCategories,
  rawMenu,
  rawNews,
  rawPages,
  rawProducts,
  tr,
  variantGroups,
  type RawProduct,
} from "./data";

const money = (n: number) => n.toFixed(2);

/** Каркас відповіді /delivery/quote — форма 1-в-1 з backend/delivery/api.py::QuoteOut. */
const emptyQuote: DeliveryQuoteOut = {
  ok: false,
  reason: null,
  cost: null,
  cost_redelivery: null,
  currency: "UAH",
  service_type: "",
  city_ref: "",
  seats_amount: 0,
  weight_kg: "0",
  volume_m3: "0",
  chargeable_weight_kg: "0",
  cost_declared: "0",
  dims_source: "mock",
  cached: false,
};
const num = (s: string) => Number.parseFloat(s);

/** Імітує мережеву затримку, щоб skeleton-стани були видимі в дев-режимі. */
const delay = (ms = 0) => (ms ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());

// ─────────────────────────────────────────────────────────────────────────────
// Мапінг RawProduct → API-типи
// ─────────────────────────────────────────────────────────────────────────────

/** Свотчі кольорів для картки у видачі (DESIGN_SYSTEM §1). */
function swatchesFor(p: RawProduct) {
  if (!p.variant || p.variant.hex === undefined) return [];
  const group = variantGroups.find((g) => g.id === p.variant!.groupId);
  if (!group || group.widget !== "swatches") return [];

  return rawProducts
    .filter((x) => x.variant?.groupId === p.variant!.groupId && x.variant.hex)
    .sort((a, b) => a.variant!.sort - b.variant!.sort)
    .map((x) => ({ hex: x.variant!.hex!, product_id: x.id }));
}

function toListItem(p: RawProduct, lang: ApiLang): ProductListItem {
  return {
    id: p.id,
    sku: p.sku,
    name: tr(p.name, lang),
    slug: p.slug,
    price: p.price,
    old_price: p.old_price,
    availability: p.availability,
    order_lead_days: p.order_lead_days,
    condition: p.condition,
    is_featured: p.is_featured,
    installment_available: p.installment !== null,
    installment_max_payments: p.installment,
    main_image_url: productImages(p)[0]?.url ?? null,
    brand: brands.find((b) => b.id === p.brandId) ?? null,
    swatches: swatchesFor(p),
  };
}

function variantGroupsFor(p: RawProduct, lang: ApiLang): VariantGroupOut[] {
  if (!p.variant) return [];
  const group = variantGroups.find((g) => g.id === p.variant!.groupId);
  if (!group) return [];

  const members = rawProducts
    .filter((x) => x.variant?.groupId === group.id)
    .sort((a, b) => a.variant!.sort - b.variant!.sort);

  return [
    {
      id: group.id,
      // Підпис осі = точний текст характеристики (INPUTS §4).
      axis_label: tr(attr(group.axisCode).label, lang),
      widget: group.widget,
      items: members.map((m) => ({
        product_id: m.id,
        label: tr(m.variant!.label, lang),
        swatch_hex: m.variant!.hex ?? null,
        slug: m.slug,
        // ⚠️ Варіант, якого немає, НЕ ховаємо — показуємо disabled+line-through:
        // людина має бачити, що 85" існує, але зараз недоступний.
        is_active: m.availability !== "out_of_stock",
        availability: m.availability,
      })),
    },
  ];
}

function toDetail(p: RawProduct, lang: ApiLang): ProductDetail {
  const cat = rawCategories.find((c) => c.id === p.categoryId)!;
  const crumbs = categoryBreadcrumbs(p.categoryId, lang);

  return {
    ...toListItem(p, lang),
    description: tr(p.description, lang),
    short_description: tr(p.short, lang),
    mpn: "",
    barcode: "",
    category: { id: cat.id, external_id: cat.external_id, name: tr(cat.name, lang), slug: cat.slug },
    breadcrumbs: crumbs,
    country: country(p.countryId, lang),
    warranty_months: p.warranty_months,
    condition_note: "",
    stock_qty: p.stock_qty,
    images: productImages(p),
    specs: buildSpecs(p, lang),
    variant_groups: variantGroupsFor(p, lang),
    package_weight_kg: p.pkg.w,
    package_width_cm: p.pkg.width,
    package_height_cm: p.pkg.height,
    package_depth_cm: p.pkg.depth,
    seo_title: tr(p.name, lang),
    seo_description: tr(p.short, lang),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Фільтрація — повторює логіку бекенда (ADR-008):
// OR у межах групи (overlap), AND між групами.
// ─────────────────────────────────────────────────────────────────────────────

function matchesFacets(p: RawProduct, facets: Record<string, string[]>): boolean {
  const tokens = new Set(buildTokens(p));

  return Object.entries(facets).every(([code, values]) => {
    if (values.length === 0) return true;
    // OR усередині групи.
    return values.some((v) => tokens.has(`${code}:${v}`));
  });
}

function sortProducts(list: RawProduct[], sort: SortKey): RawProduct[] {
  const copy = [...list];
  switch (sort) {
    case "price_asc":
      return copy.sort((a, b) => num(a.price) - num(b.price));
    case "price_desc":
      return copy.sort((a, b) => num(b.price) - num(a.price));
    case "new":
      return copy.sort((a, b) => b.id - a.id);
    case "name":
      return copy.sort((a, b) => a.name.uk.localeCompare(b.name.uk, "uk"));
    case "popular":
    default:
      return copy.sort(
        (a, b) => Number(b.is_featured) - Number(a.is_featured) || a.id - b.id,
      );
  }
}

/**
 * Фасети рахуються ПО ТОМУ Ж набору, що й результати, але БЕЗ поточної групи —
 * інакше після вибору «Bosch» усі інші бренди показали б 0 і фільтр став би тупиком.
 * Точно так само робить бекенд (один SELECT з unnest(filter_tokens)).
 */
function buildFacets(
  pool: RawProduct[],
  selected: Record<string, string[]>,
  lang: ApiLang,
): FacetGroup[] {
  const groups: FacetGroup[] = [];

  const countFor = (code: string): Map<string, number> => {
    const others = Object.fromEntries(
      Object.entries(selected).filter(([k]) => k !== code),
    );
    const subset = pool.filter((p) => matchesFacets(p, others));

    const counts = new Map<string, number>();
    for (const p of subset) {
      for (const token of buildTokens(p)) {
        const [tCode, tValue] = [token.slice(0, token.indexOf(":")), token.slice(token.indexOf(":") + 1)];
        if (tCode !== code) continue;
        counts.set(tValue, (counts.get(tValue) ?? 0) + 1);
      }
    }
    return counts;
  };

  // Бренд — окрема група (token "brand:*", не атрибут).
  const brandCounts = countFor("brand");
  if (brandCounts.size > 0) {
    groups.push({
      code: "brand",
      label: lang === "uk" ? "Виробник" : "Производитель",
      widget: "checkbox",
      unit: "",
      values: brands
        .filter((b) => brandCounts.has(b.slug))
        .map<FacetValue>((b) => ({
          value: b.slug,
          label: b.name,
          count: brandCounts.get(b.slug) ?? 0,
        }))
        .sort((a, b) => b.count - a.count),
    });
  }

  // Наявність.
  const availCounts = countFor("avail");
  if (availCounts.size > 0) {
    const labels: Record<string, { uk: string; ru: string }> = {
      in_stock: { uk: "В наявності", ru: "В наличии" },
      on_order: { uk: "Під замовлення", ru: "Под заказ" },
      out_of_stock: { uk: "Немає в наявності", ru: "Нет в наличии" },
    };
    groups.push({
      code: "avail",
      label: lang === "uk" ? "Наявність" : "Наличие",
      widget: "checkbox",
      unit: "",
      values: (["in_stock", "on_order", "out_of_stock"] as const)
        .filter((v) => availCounts.has(v))
        .map<FacetValue>((v) => ({
          value: v,
          label: tr(labels[v], lang),
          count: availCounts.get(v) ?? 0,
        })),
    });
  }

  // Атрибути з бакетами / опціями.
  for (const a of attributes) {
    const counts = countFor(a.code);
    if (counts.size === 0) continue;

    const values: FacetValue[] = a.buckets
      ? a.buckets
          .filter((b) => counts.has(b.value))
          .map((b) => ({ value: b.value, label: tr(b.label, lang), count: counts.get(b.value)! }))
      : [...counts.entries()]
          .map(([value, count]) => {
            // Підпис беремо з першого товару, що має цей токен — так само,
            // як бекенд бере AttributeOption.value_{lang}.
            const sample = pool.find((p) =>
              p.specs.some((s) => s.code === a.code && s.token === value),
            );
            const spec = sample?.specs.find((s) => s.code === a.code);
            const unit = tr(a.unit, lang);
            const label = spec
              ? `${tr(spec.value, lang)}${unit ? ` ${unit}` : ""}`
              : value;
            return { value, label, count };
          })
          .sort((x, y) => y.count - x.count);

    if (values.length === 0) continue;

    groups.push({
      code: a.code,
      label: tr(a.label, lang),
      widget: a.buckets ? "checkbox" : a.widget === "switch" ? "switch" : "checkbox",
      unit: tr(a.unit, lang),
      values,
    });
  }

  // Оплата частинами — зручний практичний фільтр.
  const instCounts = countFor("installment");
  if (instCounts.size > 0) {
    groups.push({
      code: "installment",
      label: lang === "uk" ? "Оплата частинами" : "Оплата частями",
      widget: "switch",
      unit: "",
      values: [
        {
          value: "1",
          label: lang === "uk" ? "Доступна" : "Доступна",
          count: instCounts.get("1") ?? 0,
        },
      ],
    });
  }

  return groups;
}

// ─────────────────────────────────────────────────────────────────────────────
// In-memory замовлення (живуть до перезапуску dev-сервера)
// ─────────────────────────────────────────────────────────────────────────────

const orders = new Map<string, OrderOut>();
const ordersByIdempotencyKey = new Map<string, string>();

let orderCounter = 41;

function makeOrderNumber(): string {
  const d = new Date();
  const yy = String(d.getFullYear()).slice(2);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  orderCounter += 1;
  return `CMPX-${yy}${mm}${dd}-${String(orderCounter).padStart(4, "0")}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Реалізація
// ─────────────────────────────────────────────────────────────────────────────

export const mockApi: ComplexApi = {
  async getCategoryTree(lang) {
    await delay();
    return buildCategoryTree(lang);
  },

  async getQuickNav(lang) {
    await delay();
    // У моках галочок ніхто не ставив, тому рядок під шапкою тут порожній — і це чесно:
    // на живому API він порожній рівно доти, доки замовник не відмітить категорії.
    void lang;
    return [];
  },

  async getCatalog(query: CatalogQuery, lang): Promise<CatalogResponse> {
    await delay();

    const category = query.category ? findCategory(query.category) : undefined;
    const allowed = category ? categoryWithDescendants(category.id) : null;

    let pool = rawProducts.filter((p) => (allowed ? allowed.has(p.categoryId) : true));

    if (query.q) {
      const needle = query.q.trim().toLowerCase();
      pool = pool.filter(
        (p) =>
          tr(p.name, lang).toLowerCase().includes(needle) ||
          p.name.uk.toLowerCase().includes(needle) ||
          p.sku.toLowerCase().includes(needle) ||
          (brands.find((b) => b.id === p.brandId)?.name.toLowerCase().includes(needle) ?? false),
      );
    }

    const prices = pool.map((p) => num(p.price));
    const price_range = {
      min: prices.length ? Math.floor(Math.min(...prices)) : 0,
      max: prices.length ? Math.ceil(Math.max(...prices)) : 0,
    };

    const facets = buildFacets(pool, query.facets ?? {}, lang);

    let filtered = pool.filter((p) => matchesFacets(p, query.facets ?? {}));
    if (query.price_min !== undefined) {
      filtered = filtered.filter((p) => num(p.price) >= query.price_min!);
    }
    if (query.price_max !== undefined) {
      filtered = filtered.filter((p) => num(p.price) <= query.price_max!);
    }

    const sorted = sortProducts(filtered, query.sort ?? "popular");

    const page = Math.max(1, query.page ?? 1);
    const page_size = query.page_size ?? 24;
    const total = sorted.length;
    const start = (page - 1) * page_size;

    return {
      items: sorted.slice(start, start + page_size).map((p) => toListItem(p, lang)),
      total,
      page,
      page_size,
      pages: Math.max(1, Math.ceil(total / page_size)),
      facets,
      price_range,
      category: category
        ? {
            id: category.id,
            external_id: category.external_id,
            name: tr(category.name, lang),
            slug: category.slug,
            parent_id: category.parent_id,
            depth: category.parent_id === null ? 0 : 1,
            products_count: productsCountIn(category.id),
            icon_url: null,
            image_url: null,
          }
        : null,
      breadcrumbs: category ? categoryBreadcrumbs(category.id, lang) : [],
      subcategories: category
        ? rawCategories
            .filter((c) => c.parent_id === category.id)
            .map<CategoryOut>((c) => ({
              id: c.id,
              external_id: c.external_id,
              name: tr(c.name, lang),
              slug: c.slug,
              parent_id: c.parent_id,
              depth: 1,
              products_count: productsCountIn(c.id),
              icon_url: null,
              image_url: null,
            }))
        : [],
    };
  },

  async getProduct(id, lang) {
    await delay();
    const p = rawProducts.find((x) => x.id === id);
    return p ? toDetail(p, lang) : null;
  },

  async getRelatedProducts(id, lang) {
    await delay();
    const p = rawProducts.find((x) => x.id === id);
    if (!p) return [];
    return rawProducts
      .filter((x) => x.id !== p.id && x.categoryId === p.categoryId)
      .slice(0, 8)
      .map((x) => toListItem(x, lang));
  },

  async getProductsBulk(ids, lang) {
    await delay();
    // Толерантно до неіснуючих id: зниклі просто не повертаються.
    return ids
      .map((id) => rawProducts.find((p) => p.id === id))
      .filter((p): p is RawProduct => Boolean(p))
      .map((p) => toListItem(p, lang));
  },

  async getProductDetails(ids, lang) {
    await delay();
    return ids
      .map((id) => rawProducts.find((p) => p.id === id))
      .filter((p): p is RawProduct => Boolean(p))
      .map((p) => toDetail(p, lang));
  },

  async previewCart(items, lang): Promise<CartPreviewResponse> {
    await delay();

    const resolved: CartPreviewItem[] = [];
    const unavailable_items: number[] = [];

    for (const { id, qty } of items) {
      const p = rawProducts.find((x) => x.id === id);
      if (!p) {
        unavailable_items.push(id);
        continue;
      }
      if (p.availability === "out_of_stock") {
        unavailable_items.push(id);
        continue;
      }

      const safeQty = Math.max(1, Math.min(qty, 99));
      resolved.push({
        id: p.id,
        qty: safeQty,
        name: tr(p.name, lang),
        slug: p.slug,
        sku: p.sku,
        price: p.price,
        old_price: p.old_price,
        line_total: money(num(p.price) * safeQty),
        availability: p.availability,
        installment_available: p.installment !== null,
        installment_max_payments: p.installment,
        is_active: true,
        main_image_url: productImages(p)[0]?.url ?? null,
      });
    }

    const subtotal = resolved.reduce((sum, i) => sum + num(i.line_total), 0);

    return {
      items: resolved,
      subtotal: money(subtotal),
      // ⚠️ AND по всіх позиціях — рахує СЕРВЕР, не фронт.
      installment_allowed:
        resolved.length > 0 && resolved.every((i) => i.installment_available),
      changed_items: [],
      unavailable_items,
    };
  },

  async createOrder(payload: CreateOrderRequest, lang): Promise<OrderOut> {
    await delay(300);

    // Ідемпотентність: подвійний сабміт форми не створює друге замовлення.
    const existingToken = ordersByIdempotencyKey.get(payload.idempotency_key);
    if (existingToken) {
      return orders.get(existingToken)!;
    }

    const preview = await mockApi.previewCart(payload.items, lang);
    const itemsTotal = num(preview.subtotal);

    const token = crypto.randomUUID();
    const needsPayment =
      payload.payment_method === "online" || payload.payment_method === "installment";

    const order: OrderOut = {
      number: makeOrderNumber(),
      public_token: token,
      status: "new",
      created_at: new Date().toISOString(),

      last_name: payload.last_name,
      first_name: payload.first_name,
      phone: payload.phone,
      email: payload.email ?? "",
      comment: payload.comment ?? "",

      delivery_method: payload.delivery_method,
      np_city_name: payload.np_city_name ?? "",
      np_warehouse_name: payload.np_warehouse_name ?? "",
      delivery_address: payload.delivery_address ?? "",
      ttn: null,

      payment_method: payload.payment_method,
      payment_status: needsPayment ? "pending" : "not_required",
      payment_url: null,

      items: preview.items.map((i) => ({
        product_id: i.id,
        name: i.name,
        sku: i.sku,
        qty: i.qty,
        price: i.price,
        line_total: i.line_total,
        main_image_url: i.main_image_url,
      })),
      items_total: money(itemsTotal),
      delivery_price: null,
      total: money(itemsTotal),
    };

    orders.set(token, order);
    ordersByIdempotencyKey.set(payload.idempotency_key, token);
    return order;
  },

  async getOrderByToken(token) {
    await delay();
    return orders.get(token) ?? null;
  },

  async searchCities(query): Promise<NPCityOut[]> {
    await delay();
    const needle = query.trim().toLowerCase();
    if (needle.length < 2) return [];
    return npCities.filter((c) => c.name.toLowerCase().includes(needle));
  },

  async getWarehouses(settlementRef): Promise<NPWarehouseOut[]> {
    await delay();
    return npWarehouses[settlementRef] ?? [];
  },

  async quoteDelivery(payload: DeliveryQuoteRequest): Promise<DeliveryQuoteOut> {
    await delay(400);

    // Габарити тепер бере бекенд із товару, тому мок рахує від ваги демо-товару, а
    // форму відповіді повторює 1-в-1 з backend/delivery/api.py::QuoteOut.
    const ids = [
      ...(payload.items ?? []).map((i) => i.product_id),
      ...(payload.product_id ? [payload.product_id] : []),
    ];
    if (ids.length === 0) {
      return { ...emptyQuote, ok: false, reason: "empty_cart" };
    }

    const billable = 16;
    const courierSurcharge = payload.service_type === "WarehouseDoors" ? 90 : 0;
    const cost = Math.round(60 + billable * 6.5 + courierSurcharge);

    return {
      ...emptyQuote,
      ok: true,
      cost: money(cost),
      service_type: payload.service_type || "WarehouseWarehouse",
      seats_amount: 1,
      chargeable_weight_kg: String(billable),
    };
  },

  async getBanners(lang): Promise<BannerOut[]> {
    await delay();
    return rawBanners.map((b) => ({
      id: b.id,
      title: tr(b.title, lang),
      subtitle: tr(b.subtitle, lang),
      image_url: b.image,
      mobile_image_url: null,
      link: b.link,
      cta_label: tr(b.cta, lang),
    }));
  },

  async getCollections(lang) {
    await delay();

    /**
     * Секції по КОРЕНЕВИХ категоріях — 3 найбільші, до 8 товарів у кожній.
     * Товар лежить у підкатегорії, тому шукаємо корінь через parent_id.
     */
    function mockCategorySections(l: ApiLang) {
      const rootOf = (catId: number): number => {
        const cat = rawCategories.find((c) => c.id === catId);
        return cat?.parent_id ? rootOf(cat.parent_id) : (cat?.id ?? catId);
      };

      const buckets = new Map<number, RawProduct[]>();
      for (const p of rawProducts) {
        const root = rootOf(p.categoryId);
        buckets.set(root, [...(buckets.get(root) ?? []), p]);
      }

      return [...buckets.entries()]
        .sort((a, b) => b[1].length - a[1].length)
        .slice(0, 3)
        .map(([rootId, items]) => {
          const cat = rawCategories.find((c) => c.id === rootId)!;
          return {
            id: cat.id,
            name: tr(cat.name, l),
            slug: cat.slug,
            products: items.slice(0, 8).map((p) => toListItem(p, l)),
          };
        });
    }

    const sale = rawProducts
      .filter((p) => p.old_price !== null && num(p.old_price) > num(p.price))
      .sort((a, b) => num(b.old_price!) - num(b.price) - (num(a.old_price!) - num(a.price)));

    return {
      featured: rawProducts.filter((p) => p.is_featured).map((p) => toListItem(p, lang)),
      sale: sale.map((p) => toListItem(p, lang)),
      // ⚠️ У моках немає created_at — «новинки» імітуємо спаданням id. Реальний бекенд
      //    сортує за created_at (catalog/services/collections.py), і це ЄДИНА добірка,
      //    де мок і прод розходяться в порядку. Для верстки це не має значення.
      new: [...rawProducts].sort((a, b) => b.id - a.id).slice(0, 12).map((p) => toListItem(p, lang)),
      clearance: rawProducts.filter((p) => p.condition !== 0).map((p) => toListItem(p, lang)),
      by_category: mockCategorySections(lang),
    };
  },

  async getBrands(_lang, limit = 12) {
    await delay();

    const counts = new Map<number, number>();
    for (const p of rawProducts) {
      counts.set(p.brandId, (counts.get(p.brandId) ?? 0) + 1);
    }

    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit)
      .flatMap(([id, products_count]) => {
        const brand = brands.find((b) => b.id === id);
        // Бренд без картки в довіднику просто випадає — краще коротший рядок,
        // ніж плитка з `undefined` у назві.
        return brand ? [{ ...brand, products_count }] : [];
      });
  },

  async getFeaturedProducts(lang) {
    await delay();
    return rawProducts.filter((p) => p.is_featured).map((p) => toListItem(p, lang));
  },

  async getDiscountedProducts(lang) {
    await delay();
    return rawProducts
      .filter((p) => p.old_price !== null)
      .sort((a, b) => num(b.old_price!) - num(b.price) - (num(a.old_price!) - num(a.price)))
      .map((p) => toListItem(p, lang));
  },

  async getNews(lang): Promise<NewsPostOut[]> {
    await delay();
    return [...rawNews]
      .sort((a, b) => b.published_at.localeCompare(a.published_at))
      .map((n) => ({
        id: n.id,
        title: tr(n.title, lang),
        slug: n.slug,
        excerpt: tr(n.excerpt, lang),
        body: tr(n.body, lang),
        cover_url: `/images/mock/news-${n.id}.svg`,
        published_at: n.published_at,
      }));
  },

  async getNewsPost(slug, lang) {
    await delay();
    const n = rawNews.find((x) => x.slug === slug);
    if (!n) return null;
    return {
      id: n.id,
      title: tr(n.title, lang),
      slug: n.slug,
      excerpt: tr(n.excerpt, lang),
      body: tr(n.body, lang),
      cover_url: `/images/mock/news-${n.id}.svg`,
      published_at: n.published_at,
    };
  },

  async getStaticPage(slug, lang): Promise<StaticPageOut | null> {
    await delay();
    const p = rawPages.find((x) => x.slug === slug);
    if (!p) return null;
    return {
      id: p.id,
      title: tr(p.title, lang),
      slug: p.slug,
      body: tr(p.body, lang),
      updated_at: "2026-07-01T00:00:00Z",
    };
  },

  async getMenuItems(lang): Promise<MenuItemOut[]> {
    await delay();
    return rawMenu.map((m) => ({
      id: m.id,
      title: tr(m.title, lang),
      url: `/page/${m.slug}`,
      block: m.block,
    }));
  },

  async getContacts(lang): Promise<ContactsOut> {
    await delay();
    return {
      phones: contacts.phones,
      email: contacts.email,
      address: tr(contacts.address, lang),
      // Час роботи — з INPUTS §4 (скрін бургер-меню).
      working_hours: [
        { days: lang === "uk" ? "ПН – ПТ" : "ПН – ПТ", time: "09:00 – 20:00" },
        { days: lang === "uk" ? "СБ – НД" : "СБ – ВС", time: "10:00 – 17:00" },
      ],
    };
  },
};

/** Резолвер slug-шляху каталогу → external_id категорії. Потрібен роуту /catalog/[...slug]. */
export function resolveCatalogSlug(slugs: string[]): string | undefined {
  return findCategoryBySlugPath(slugs)?.external_id;
}

/**
 * ДОМЕННІ типи — те, що споживають компоненти.
 *
 * Не плутати з `wire.ts`: там форма, яку РЕАЛЬНО віддає Django Ninja. Дві форми
 * майже збігаються, і в трьох місцях — ні (specs, bulk, search). Перетворення робить
 * ВИКЛЮЧНО `http.ts` (адаптери). Компоненти про існування wire-типів не знають.
 *
 * Джерело: /api/v1/openapi.json + backend/catalog/schemas.py, cms/schemas.py.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Довідники / enum-и (1:1 з catalog/enums.py, orders/models.py)
// ─────────────────────────────────────────────────────────────────────────────

export type Availability = "in_stock" | "on_order" | "out_of_stock";

/** catalog.enums.Condition (IntegerChoices, сумісний з <condition> Hotline). */
export type Condition = 0 | 1 | 2 | 3;

export type SortKey = "popular" | "price_asc" | "price_desc" | "new" | "name";

export type ApiLang = "uk" | "ru";

// ─────────────────────────────────────────────────────────────────────────────
// Каталог
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Рядок specs_json (див. коментар до Product.specs_json у backend/catalog/models.py).
 *
 * ⚠️ ПРАВИЛО РЕНДЕРУ (INPUTS §2 — жорстка вимога замовника):
 *   ліва колонка  = n              → «Висота»
 *   права колонка = `${v} ${u}`    → «284 мм»
 * Фронт НІКОЛИ не конкатенує n + u. Одиниця клеїться до ЗНАЧЕННЯ.
 * Робить це formatSpecValue() у lib/format.ts — руками не збирати.
 */
export type SpecRow = {
  /** Група: «Основні», «Габаритні розміри». */
  g: string;
  /** Порядок групи. */
  gs: number;
  /** Код атрибута (мовонезалежний slug), напр. "obiem". */
  code: string;
  /** Назва БЕЗ одиниці. */
  n: string;
  /** Одиниця виміру ("мм", "л"); порожній рядок — якщо немає. */
  u: string;
  /** Значення як рядок — «як показувати». */
  v: string;
  /** Числове значення (для порівняння «краще/гірше»); null для нечислових. */
  vn: number | null;
  /** Порядок усередині групи. */
  s: number;
};

export type ImageOut = {
  id: number;
  url: string;
  alt: string;
  width: number | null;
  height: number | null;
  sort_order: number;
  is_main?: boolean;
};

export type BrandOut = {
  id: number;
  name: string;
  slug: string;
  logo_url: string | null;
};

/**
 * Бренд у рядку брендів на головній.
 *
 * ⚠️ `logo_url` у всіх 49 брендів порожній, і це не тимчасово: чужі логотипи — чужа
 * власність, качати їх «щоб було красиво» ми не будемо. Тому рядок брендів набирається
 * ТИПОГРАФІКОЮ (назва + кількість товарів), а не картинками.
 */
export type BrandListItem = BrandOut & { products_count: number };

export type CountryOut = {
  id: number;
  code: string;
  name: string;
  slug: string;
};

export type CategoryOut = {
  id: number;
  /** external_id з Google Sheets — саме він у URL каталогу. */
  external_id: string;
  name: string;
  slug: string;
  parent_id: number | null;
  depth: number;
  products_count: number;
  icon_url: string | null;
  image_url: string | null;
  /** Галочка «Показувати в рядку під шапкою» в адмінці. */
  show_in_quick_nav?: boolean;
  /** Гілка дерева. У мегаменю приходить одразу з дітьми. */
  children?: CategoryOut[];
};

/** Хлібні крихти категорії — від кореня до поточної. */
export type BreadcrumbOut = {
  id: number;
  external_id: string;
  name: string;
  slug: string;
};

export type VariantWidget = "buttons" | "swatches";

export type VariantItemOut = {
  product_id: number;
  /** '50" (127 см)' | «Слонова кістка» */
  label: string;
  /** Тільки для widget="swatches". */
  swatch_hex: string | null;
  slug: string;
  is_active: boolean;
  /** Товар існує, але зараз недоступний → показуємо перекресленим, НЕ ховаємо. */
  availability: Availability;
  /** Це і є поточний товар — бекенд рахує сам (не порівнюємо id на фронті). */
  is_current?: boolean;
};

/**
 * Група варіантів. Вісь — БУДЬ-ЯКА характеристика (INPUTS §4):
 * і діагональ (кнопки), і колір (кружечки).
 */
export type VariantGroupOut = {
  id: number;
  /** Підпис осі — точний текст характеристики: «Діагональ екрану, дюймів" (см)». */
  axis_label: string;
  widget: VariantWidget;
  items: VariantItemOut[];
};

/** Картка товару в сітці — мінімум полів, без specs. */
export type ProductListItem = {
  id: number;
  sku: string;
  name: string;
  slug: string;
  price: string;
  old_price: string | null;
  availability: Availability;
  order_lead_days: number | null;
  condition: Condition;
  is_featured: boolean;
  installment_available: boolean;
  installment_max_payments: number | null;
  main_image_url: string | null;
  brand: BrandOut | null;
  /** Свотчі кольорів прямо у видачі (DESIGN_SYSTEM §1). */
  swatches: { hex: string; product_id: number }[];
};

/**
 * Добірки для головної (GET /products/collections).
 *
 * ⚠️ `sale` ≠ `clearance`. `sale` — акційна ЦІНА (є old_price), `clearance` — фізичний СТАН
 * товару (уцінений / відновлений / б/в). Один і той самий холодильник може бути в обох, і це
 * різні причини його купити. Легасі-роут /products/discounted означає саме `sale`.
 *
 * ⚠️ Порожній масив — НОРМА, а не помилка: у БД може не бути жодного товару з галочкою
 * «Рекомендований». Фронт тоді просто не рендерить блок (див. app/[locale]/page.tsx).
 */
/** Секція головної, зібрана по КАТЕГОРІЇ: заголовок + до 8 товарів + шлях у категорію. */
export type CategorySection = {
  id: number;
  name: string;
  slug: string;
  products: ProductListItem[];
};

export type CollectionsOut = {
  /** «Хіти продажів» — Product.is_featured (галочка в адмінці). */
  featured: ProductListItem[];
  /** «Акції» — є old_price, і вона більша за price (гарантія CheckConstraint у БД). */
  sale: ProductListItem[];
  /** «Новинки» — за Product.created_at. */
  new: ProductListItem[];
  /** «Уцінка» — Product.condition ≠ «Новий». */
  clearance: ProductListItem[];
  /**
   * Найбільші категорії з товарами — те, чим головна живе, поки замовник не проставив
   * жодної галочки «Рекомендований» і жодної старої ціни (зараз саме так: 0 і 0).
   */
  by_category: CategorySection[];
};

/** Ключ добірки — щоб мапу секцій на головній не можна було зібрати з опечаткою. */
/**
 * Ключі ДОБІРОК ЗА ПРАПОРЦЕМ товару. `by_category` сюди свідомо не входить: воно має
 * іншу форму (секція з назвою й посиланням), і `keyof CollectionsOut` затягнув би його
 * у місця, які чекають плоский `ProductListItem[]`.
 */
export type CollectionKey = "featured" | "sale" | "new" | "clearance";

/** Повна картка товару. */
export type ProductDetail = ProductListItem & {
  description: string;
  short_description: string;
  mpn: string;
  barcode: string;
  category: BreadcrumbOut;
  breadcrumbs: BreadcrumbOut[];
  country: CountryOut | null;
  warranty_months: number | null;
  condition_note: string;
  stock_qty: number | null;
  images: ImageOut[];
  specs: SpecRow[];
  variant_groups: VariantGroupOut[];
  /** Габарити упаковки — вхід для DeliveryCalculator (INPUTS §3.4). */
  package_weight_kg: string | null;
  package_width_cm: string | null;
  package_height_cm: string | null;
  package_depth_cm: string | null;
  seo_title: string;
  seo_description: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Фасети
// ─────────────────────────────────────────────────────────────────────────────

export type FacetWidget = "checkbox" | "range_slider" | "switch";

export type FacetValue = {
  /** Мовонезалежний token-suffix: "bosch", "300-399", "1". */
  value: string;
  /** Локалізований підпис: «Bosch», «300–399 л». */
  label: string;
  count: number;
};

export type FacetGroup = {
  /** Ключ групи в query-string: "brand", "obiem", "avail". */
  code: string;
  label: string;
  widget: FacetWidget;
  unit: string;
  /** Секція характеристик, до якої належить фасет: «Основні», «Функції та можливості». */
  group?: string;
  values: FacetValue[];
  /** Для range_slider. */
  min?: number;
  max?: number;
};

export type PriceRange = { min: number; max: number };

// ─────────────────────────────────────────────────────────────────────────────
// Списки
// ─────────────────────────────────────────────────────────────────────────────

export type Paginated<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
};

export type CatalogQuery = {
  /** external_id категорії. */
  category?: string;
  /** Пошуковий запит. */
  q?: string;
  page?: number;
  page_size?: number;
  sort?: SortKey;
  price_min?: number;
  price_max?: number;
  /**
   * Обрані фасети: { brand: ["bosch","gorenje"], obiem: ["300-399"] }.
   * OR у межах групи (`&&`), AND між групами — рахує бекенд (ADR-008).
   */
  facets?: Record<string, string[]>;
};

export type CatalogResponse = Paginated<ProductListItem> & {
  facets: FacetGroup[];
  price_range: PriceRange;
  category: CategoryOut | null;
  breadcrumbs: BreadcrumbOut[];
  subcategories: CategoryOut[];
};

// ─────────────────────────────────────────────────────────────────────────────
// Кошик: bulk-перевірка (ARCHITECTURE §2.3, ADR по localStorage)
// ─────────────────────────────────────────────────────────────────────────────

export type CartPreviewRequestItem = { id: number; qty: number };

export type CartPreviewItem = {
  id: number;
  qty: number;
  name: string;
  slug: string;
  sku: string;
  /** АКТУАЛЬНА ціна з БД — не з localStorage. */
  price: string;
  old_price: string | null;
  line_total: string;
  availability: Availability;
  installment_available: boolean;
  installment_max_payments: number | null;
  is_active: boolean;
  main_image_url: string | null;
};

/**
 * ⚠️ Відповідь POST /api/cart/preview.
 *
 * Причина існування ендпоінта: у localStorage тримаємо ТІЛЬКИ {id, qty}.
 * Ціна й наявність — завжди з сервера, інакше показуватимемо протухлі ціни.
 * `installment_allowed` — AND по всіх позиціях, рахує СЕРВЕР, не фронт.
 */
export type CartPreviewResponse = {
  items: CartPreviewItem[];
  subtotal: string;
  /** AND по всіх позиціях. Бекенд віддає це саме значення ще й як `installment_available_for_all`. */
  installment_allowed: boolean;
  /** Позиції, чия ціна змінилась відносно переданої клієнтом (якщо клієнт її передав). */
  changed_items: number[];
  /** id, яких більше немає / вони неактивні — фронт їх прибирає з кошика. */
  unavailable_items: number[];
};

// ─────────────────────────────────────────────────────────────────────────────
// Замовлення
// ─────────────────────────────────────────────────────────────────────────────

export type OrderStatus =
  | "new"
  | "confirmed"
  | "packed"
  | "shipped"
  | "delivered"
  | "done"
  | "cancelled"
  | "returned";

export type DeliveryMethod =
  | "np_warehouse"
  | "np_postomat"
  | "np_courier"
  | "pickup"
  | "local_courier";

export type PaymentMethod = "cod" | "prepay" | "online" | "installment";

export type PaymentStatus = "not_required" | "pending" | "paid" | "failed" | "refunded";

export type CreateOrderRequest = {
  items: CartPreviewRequestItem[];
  /** Сервер перерахує total з БД; розбіжність → 409 {changed_items}. */
  expected_total: string;
  /** Клієнт генерує РАЗ на checkout. Подвійний сабміт не створює друге замовлення. */
  idempotency_key: string;

  last_name: string;
  first_name: string;
  phone: string;
  email?: string;
  comment?: string;

  delivery_method: DeliveryMethod;
  np_city_ref?: string;
  np_city_name?: string;
  np_warehouse_ref?: string;
  np_warehouse_name?: string;
  delivery_address?: string;

  payment_method: PaymentMethod;
};

export type OrderItemOut = {
  product_id: number;
  name: string;
  sku: string;
  qty: number;
  price: string;
  line_total: string;
  main_image_url: string | null;
};

export type OrderOut = {
  number: string;
  /**
   * ⚠️ Публічний роут — /order/{public_token}, НЕ /order/{number}.
   * Номер передбачуваний (дата + лічильник): перебір віддав би ПІБ/телефон/адресу
   * будь-кого. Токен — 122 біти ентропії.
   */
  public_token: string;
  status: OrderStatus;
  created_at: string;

  last_name: string;
  first_name: string;
  phone: string;
  email: string;
  comment: string;

  delivery_method: DeliveryMethod;
  np_city_name: string;
  np_warehouse_name: string;
  delivery_address: string;
  ttn: string | null;

  payment_method: PaymentMethod;
  payment_status: PaymentStatus;
  /** Куди редіректити на оплату (LiqPay), якщо потрібно. */
  payment_url: string | null;

  items: OrderItemOut[];
  items_total: string;
  delivery_price: string | null;
  total: string;
};

/** 409 від POST /api/orders — ціни змінились між preview і сабмітом. */
export type OrderConflict = {
  detail: "price_changed";
  changed_items: number[];
  actual_total: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Доставка (Нова Пошта)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * ⚠️ Типи доставки приведені до РЕАЛЬНОЇ схеми бекенда (backend/delivery/api.py).
 * Раніше вони описували вигаданий контракт (`city_ref`, `price`, `days_min/days_max`),
 * якого на бекенді не існує ЖОДНОГО поля — бо фронт ходив не в API, а в мок.
 */

/** GET /delivery/settlements — населений пункт (SettlementRef, НЕ CityRef!). */
export type NPCityOut = {
  ref: string;
  /** 🔴 Канонічний CityRef; бекенд підставляє його сам, фронт передає назад саме `ref`. */
  delivery_city_ref: string;
  name: string;
  /** «м. Ужгород, Закарпатська обл.» — повний підпис для автокомпліта. */
  present: string;
  area: string;
  warehouses_count: number;
};

/** GET /delivery/warehouses — відділення / поштомат / пункт видачі. */
export type NPWarehouseOut = {
  ref: string;
  number: string;
  description: string;
  short_address: string;
  category: string;
  /** WarehouseWarehouse | WarehousePostomat | WarehouseDoors — має відповідати ТОЧЦІ. */
  service_type: string;
  max_width_cm: number | null;
  max_height_cm: number | null;
  max_length_cm: number | null;
  place_max_weight_kg: number | null;
};

/**
 * POST /delivery/quote. Габарити НЕ шлемо: бекенд бере їх з товару сам
 * (`effective_dims`) — це єдине джерело правди, і воно вміє фолбек на дефолти категорії.
 */
export type DeliveryQuoteRequest = {
  settlement_ref?: string;
  warehouse_ref?: string;
  product_id?: number;
  qty?: number;
  items?: { product_id: number; qty: number }[];
  service_type?: string;
  redelivery?: string | null;
};

/** 🔴 ЗАВЖДИ 200, навіть коли НП лежить: `ok:false` + `reason` (backend/delivery/api.py). */
export type DeliveryQuoteOut = {
  ok: boolean;
  reason: string | null;
  cost: string | null;
  cost_redelivery: string | null;
  currency: string;
  service_type: string;
  city_ref: string;
  seats_amount: number;
  weight_kg: string;
  volume_m3: string;
  /** max(фактична, об'ємна) — саме від цього числа НП рахує ціну. */
  chargeable_weight_kg: string;
  cost_declared: string;
  dims_source: string;
  cached: boolean;
};

// ─────────────────────────────────────────────────────────────────────────────
// CMS
// ─────────────────────────────────────────────────────────────────────────────

export type BannerOut = {
  id: number;
  title: string;
  subtitle: string;
  image_url: string;
  mobile_image_url: string | null;
  link: string;
  cta_label: string;
  /** "home_hero" | "home_promo" — місце на сторінці (Banner.placement у БД). */
  placement?: string;
  /** Кадр у відсотках → object-position: {x}% {y}%. Ставиться в адмінці кліком по фото. */
  focus_x?: number;
  focus_y?: number;
  /** Наближення, % (100 = як є). Обрізає сильніше, але дає прибрати зайве по краях. */
  zoom?: number;
};

export type NewsPostOut = {
  id: number;
  title: string;
  slug: string;
  excerpt: string;
  /** Rich HTML — санітизований на бекенді. */
  body: string;
  cover_url: string | null;
  published_at: string;
};

export type StaticPageOut = {
  id: number;
  title: string;
  slug: string;
  /** Rich HTML — санітизований на бекенді. */
  body: string;
  updated_at: string;
};

export type MenuItemOut = {
  id: number;
  title: string;
  url: string;
  /** "info" | "buyers" — блок бургер-меню / футера. */
  block: string;
};

export type ContactsOut = {
  phones: string[];
  email: string;
  address: string;
  working_hours: { days: string; time: string }[];
};

// ─────────────────────────────────────────────────────────────────────────────
// Помилки
// ─────────────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

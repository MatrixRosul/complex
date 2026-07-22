import type {
  ApiLang,
  BannerOut,
  BrandListItem,
  CartPreviewRequestItem,
  CartPreviewResponse,
  CatalogQuery,
  CatalogResponse,
  CategoryOut,
  CollectionsOut,
  ContactsOut,
  CreateOrderRequest,
  DeliveryQuoteOut,
  DeliveryQuoteRequest,
  MenuItemOut,
  NewsPostOut,
  NPCityOut,
  NPWarehouseOut,
  OrderOut,
  ProductDetail,
  ProductListItem,
  StaticPageOut,
} from "./types";

/**
 * Єдиний контракт даних. Реалізацій дві:
 *   - http (lib/api/http.ts)       — Django Ninja /api/v1 (ДЕФОЛТ);
 *   - mock (lib/api/mock/index.ts) — статичні дані; лишились як аварійний режим
 *     (NEXT_PUBLIC_USE_MOCKS=true) і як джерело для ще НЕ написаних роутерів
 *     (orders, delivery — див. коментар у http.ts).
 *
 * Сторінки імпортують ТІЛЬКИ `api` з lib/api — вони не знають, яка реалізація під ними.
 */
export interface ComplexApi {
  // ── Каталог ────────────────────────────────────────────────────────────
  getCategoryTree(lang: ApiLang): Promise<CategoryOut[]>;

  /**
   * Рядок розділів під шапкою — РІВНО ті категорії, які замовник відмітив в адмінці
   * галочкою «Показувати в рядку під шапкою». Плоский список, порядок уже правильний.
   *
   * ⚠️ Окремий виклик, а не фільтр по `getCategoryTree`: відмітити можна й ПІДкатегорію,
   * тобто фронту довелося б рекурсивно обходити дерево на кожному рендері шапки — на
   * кожній сторінці сайту. Обхід робить сервер один раз на нагрів кешу.
   */
  getQuickNav(lang: ApiLang): Promise<CategoryOut[]>;
  getCatalog(query: CatalogQuery, lang: ApiLang): Promise<CatalogResponse>;
  getProduct(id: number, lang: ApiLang): Promise<ProductDetail | null>;
  getRelatedProducts(id: number, lang: ApiLang): Promise<ProductListItem[]>;

  /**
   * Bulk для /wishlist: POST /products/bulk → картки з АКТУАЛЬНОЮ ціною й наявністю.
   *
   * ⚠️ Той самий ендпоінт, що й у кошика, і з тієї ж причини: у localStorage лежать
   * голі id. Список бажань живе місяцями — ціна в ньому протухає гарантовано.
   * Толерантний до зниклих id: вони не 404, а йдуть у `unavailable_items` і сюди не потрапляють.
   */
  getProductsBulk(ids: number[], lang: ApiLang): Promise<ProductListItem[]>;

  /**
   * Повні картки за списком id — для /compare, якому потрібні ХАРАКТЕРИСТИКИ
   * (bulk їх не віддає: у сітці й кошику вони нікому не потрібні, а це +40 рядків на товар).
   * Порівняння обмежене 8 товарами, тому N запитів тут — це максимум 8.
   */
  getProductDetails(ids: number[], lang: ApiLang): Promise<ProductDetail[]>;

  // ── Кошик ──────────────────────────────────────────────────────────────
  /**
   * ⚠️ ЄДИНЕ джерело цін і наявності для кошика.
   * localStorage тримає тільки {id, qty} — інакше показували б протухлі ціни.
   */
  previewCart(items: CartPreviewRequestItem[], lang: ApiLang): Promise<CartPreviewResponse>;

  // ── Замовлення ─────────────────────────────────────────────────────────
  createOrder(payload: CreateOrderRequest, lang: ApiLang): Promise<OrderOut>;
  /** Роут — по ПУБЛІЧНОМУ ТОКЕНУ, не по номеру (нуль IDOR). */
  getOrderByToken(token: string, lang: ApiLang): Promise<OrderOut | null>;

  // ── Доставка (реальні роути /api/v1/delivery/*, НЕ мок) ────────────────
  /** Автокомпліт населеного пункту. Менше 2 символів — порожній список, без запиту. */
  searchCities(query: string): Promise<NPCityOut[]>;
  /** 🔴 Саме SettlementRef (`NPCityOut.ref`), а не CityRef — див. INTEGRATIONS §1.4. */
  getWarehouses(settlementRef: string): Promise<NPWarehouseOut[]>;
  /** Завжди резолвиться (200): при збої НП — `{ok:false, reason}`, а не виняток. */
  quoteDelivery(payload: DeliveryQuoteRequest): Promise<DeliveryQuoteOut>;

  // ── CMS ────────────────────────────────────────────────────────────────
  getBanners(lang: ApiLang): Promise<BannerOut[]>;
  /**
   * Усі чотири добірки головної ОДНИМ запитом (хіти / акції / новинки / уцінка).
   * Порожня добірка = `[]`, і головна просто не рендерить її блок.
   */
  getCollections(lang: ApiLang): Promise<CollectionsOut>;

  /** Топ брендів за кількістю товарів — рядок брендів на головній. */
  getBrands(lang: ApiLang, limit?: number): Promise<BrandListItem[]>;
  /** @deprecated Легасі. Це `getCollections().featured` — лишений заради сумісності. */
  getFeaturedProducts(lang: ApiLang): Promise<ProductListItem[]>;
  /** @deprecated Легасі. Це `getCollections().sale` (є old_price), а НЕ уцінка. */
  getDiscountedProducts(lang: ApiLang): Promise<ProductListItem[]>;
  getNews(lang: ApiLang): Promise<NewsPostOut[]>;
  getNewsPost(slug: string, lang: ApiLang): Promise<NewsPostOut | null>;
  getStaticPage(slug: string, lang: ApiLang): Promise<StaticPageOut | null>;
  getMenuItems(lang: ApiLang): Promise<MenuItemOut[]>;
  getContacts(lang: ApiLang): Promise<ContactsOut>;
}

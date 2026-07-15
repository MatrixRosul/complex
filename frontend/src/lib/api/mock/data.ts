/**
 * Моки. Форма даних — точно як у реального API (див. ../types.ts), включно з
 * пастками з INPUTS.md: одиниця виміру ЗАВЖДИ в окремому полі `u`, ніколи не в назві;
 * ціни — рядки (Decimal з Django), а не number.
 *
 * Двомовність тримаємо чесно: кожен перекладний рядок має uk/ru варіант.
 */

import type {
  Availability,
  BannerOut,
  BrandOut,
  CategoryOut,
  Condition,
  CountryOut,
  ApiLang,
  MenuItemOut,
  NewsPostOut,
  NPCityOut,
  NPWarehouseOut,
  SpecRow,
  StaticPageOut,
  VariantWidget,
} from "../types";

/** Хелпер: перекладний рядок. */
export type TR = { uk: string; ru: string };
export const tr = (value: TR, lang: ApiLang): string => value[lang];

// ─────────────────────────────────────────────────────────────────────────────
// Довідники
// ─────────────────────────────────────────────────────────────────────────────

export const brands: BrandOut[] = [
  { id: 1, name: "Bosch", slug: "bosch", logo_url: null },
  { id: 2, name: "Gorenje", slug: "gorenje", logo_url: null },
  { id: 3, name: "LG", slug: "lg", logo_url: null },
  { id: 4, name: "Samsung", slug: "samsung", logo_url: null },
  { id: 5, name: "Siemens", slug: "siemens", logo_url: null },
  { id: 6, name: "Electrolux", slug: "electrolux", logo_url: null },
];

const countriesRaw: { id: number; code: string; slug: string; name: TR }[] = [
  { id: 1, code: "DE", slug: "nimechchyna", name: { uk: "Німеччина", ru: "Германия" } },
  { id: 2, code: "SI", slug: "sloveniia", name: { uk: "Словенія", ru: "Словения" } },
  { id: 3, code: "KR", slug: "pivdenna-koreia", name: { uk: "Південна Корея", ru: "Южная Корея" } },
  { id: 4, code: "PL", slug: "polshcha", name: { uk: "Польща", ru: "Польша" } },
  { id: 5, code: "RS", slug: "serbiia", name: { uk: "Сербія", ru: "Сербия" } },
];

export const country = (id: number, lang: ApiLang): CountryOut | null => {
  const c = countriesRaw.find((x) => x.id === id);
  return c ? { id: c.id, code: c.code, name: tr(c.name, lang), slug: c.slug } : null;
};

// ─────────────────────────────────────────────────────────────────────────────
// Категорії. external_id — рядковий id з Google Sheets (саме він у URL каталогу).
// ─────────────────────────────────────────────────────────────────────────────

type RawCategory = {
  id: number;
  external_id: string;
  slug: string;
  name: TR;
  parent_id: number | null;
  /** Ключ міні-емблеми (lucide) — мегаменю, DESIGN_SYSTEM §4.9. */
  icon: string;
};

export const rawCategories: RawCategory[] = [
  // ── Корені ──
  { id: 1, external_id: "c1000", slug: "velyka-pobutova-tehnika", name: { uk: "Велика побутова техніка", ru: "Крупная бытовая техника" }, parent_id: null, icon: "refrigerator" },
  { id: 2, external_id: "c2000", slug: "mala-pobutova-tehnika", name: { uk: "Мала побутова техніка", ru: "Мелкая бытовая техника" }, parent_id: null, icon: "coffee" },
  { id: 3, external_id: "c3000", slug: "tv-audio-video", name: { uk: "ТВ, аудіо, відео", ru: "ТВ, аудио, видео" }, parent_id: null, icon: "tv" },
  { id: 4, external_id: "c4000", slug: "vbudovana-tehnika", name: { uk: "Вбудована техніка", ru: "Встраиваемая техника" }, parent_id: null, icon: "layout-grid" },
  { id: 5, external_id: "c5000", slug: "klimatychna-tehnika", name: { uk: "Кліматична техніка", ru: "Климатическая техника" }, parent_id: null, icon: "wind" },
  { id: 6, external_id: "c6000", slug: "aksesuary", name: { uk: "Аксесуари", ru: "Аксессуары" }, parent_id: null, icon: "plug" },

  // ── Велика побутова техніка ──
  { id: 11, external_id: "c1010", slug: "holodylnyky", name: { uk: "Холодильники", ru: "Холодильники" }, parent_id: 1, icon: "refrigerator" },
  { id: 12, external_id: "c1020", slug: "pralni-mashyny", name: { uk: "Пральні машини", ru: "Стиральные машины" }, parent_id: 1, icon: "washing-machine" },
  { id: 13, external_id: "c1030", slug: "posudomyiky", name: { uk: "Посудомийні машини", ru: "Посудомоечные машины" }, parent_id: 1, icon: "utensils" },
  { id: 14, external_id: "c1040", slug: "plyty", name: { uk: "Плити", ru: "Плиты" }, parent_id: 1, icon: "flame" },
  { id: 15, external_id: "c1050", slug: "morozylni-kamery", name: { uk: "Морозильні камери", ru: "Морозильные камеры" }, parent_id: 1, icon: "snowflake" },
  { id: 16, external_id: "c1060", slug: "sushylni-mashyny", name: { uk: "Сушильні машини", ru: "Сушильные машины" }, parent_id: 1, icon: "shirt" },

  // ── Мала побутова техніка ──
  { id: 21, external_id: "c2010", slug: "mikrohvylovi-pechi", name: { uk: "Мікрохвильові печі", ru: "Микроволновые печи" }, parent_id: 2, icon: "microwave" },
  { id: 22, external_id: "c2020", slug: "pylososy", name: { uk: "Пилососи", ru: "Пылесосы" }, parent_id: 2, icon: "bot" },
  { id: 23, external_id: "c2030", slug: "kavomashyny", name: { uk: "Кавомашини", ru: "Кофемашины" }, parent_id: 2, icon: "coffee" },
  { id: 24, external_id: "c2040", slug: "blendery", name: { uk: "Блендери", ru: "Блендеры" }, parent_id: 2, icon: "blend" },
  { id: 25, external_id: "c2050", slug: "chainyky", name: { uk: "Електрочайники", ru: "Электрочайники" }, parent_id: 2, icon: "cup-soda" },

  // ── ТВ, аудіо, відео ──
  { id: 31, external_id: "c3010", slug: "televizory", name: { uk: "Телевізори", ru: "Телевизоры" }, parent_id: 3, icon: "tv" },
  { id: 32, external_id: "c3020", slug: "sauandbary", name: { uk: "Саундбари", ru: "Саундбары" }, parent_id: 3, icon: "speaker" },
  { id: 33, external_id: "c3030", slug: "navushnyky", name: { uk: "Навушники", ru: "Наушники" }, parent_id: 3, icon: "headphones" },

  // ── Вбудована техніка ──
  { id: 41, external_id: "c4010", slug: "duhovi-shafy", name: { uk: "Духові шафи", ru: "Духовые шкафы" }, parent_id: 4, icon: "oven" },
  { id: 42, external_id: "c4020", slug: "varylni-paneli", name: { uk: "Варильні панелі", ru: "Варочные панели" }, parent_id: 4, icon: "flame" },
  { id: 43, external_id: "c4030", slug: "vytiazhky", name: { uk: "Витяжки", ru: "Вытяжки" }, parent_id: 4, icon: "fan" },

  // ── Кліматична техніка ──
  { id: 51, external_id: "c5010", slug: "kondytsionery", name: { uk: "Кондиціонери", ru: "Кондиционеры" }, parent_id: 5, icon: "air-vent" },
  { id: 52, external_id: "c5020", slug: "obihrivachi", name: { uk: "Обігрівачі", ru: "Обогреватели" }, parent_id: 5, icon: "thermometer-sun" },

  // ── Аксесуари ──
  { id: 61, external_id: "c6010", slug: "aksesuary-do-tehniky", name: { uk: "Аксесуари до техніки", ru: "Аксессуары к технике" }, parent_id: 6, icon: "plug" },
];

/** Мапа icon-ключів — щоб мегаменю не тягнув усю бібліотеку іконок. */
export const categoryIcon = (externalId: string): string =>
  rawCategories.find((c) => c.external_id === externalId)?.icon ?? "package";

export function buildCategoryTree(lang: ApiLang): CategoryOut[] {
  const toOut = (c: RawCategory): CategoryOut => ({
    id: c.id,
    external_id: c.external_id,
    name: tr(c.name, lang),
    slug: c.slug,
    parent_id: c.parent_id,
    depth: c.parent_id === null ? 0 : 1,
    products_count: productsCountIn(c.id),
    icon_url: null,
    image_url: null,
    children: rawCategories.filter((x) => x.parent_id === c.id).map(toOut),
  });

  return rawCategories.filter((c) => c.parent_id === null).map(toOut);
}

export function findCategory(externalId: string): RawCategory | undefined {
  return rawCategories.find((c) => c.external_id === externalId);
}

export function findCategoryBySlugPath(slugs: string[]): RawCategory | undefined {
  // Каталог живе за /catalog/<slug-кореня>/<slug-підкатегорії>, останній сегмент — цільовий.
  const last = slugs.at(-1);
  return rawCategories.find((c) => c.slug === last);
}

export function categoryBreadcrumbs(catId: number, lang: ApiLang) {
  const chain: RawCategory[] = [];
  let current = rawCategories.find((c) => c.id === catId);
  while (current) {
    chain.unshift(current);
    current = current.parent_id
      ? rawCategories.find((c) => c.id === current!.parent_id)
      : undefined;
  }
  return chain.map((c) => ({
    id: c.id,
    external_id: c.external_id,
    name: tr(c.name, lang),
    slug: c.slug,
  }));
}

/** Повний slug-шлях категорії для URL: ["velyka-pobutova-tehnika", "holodylnyky"]. */
export function categorySlugPath(catId: number): string[] {
  return categoryBreadcrumbs(catId, "uk").map((c) => c.slug);
}

// ─────────────────────────────────────────────────────────────────────────────
// Атрибути й фасети
// ─────────────────────────────────────────────────────────────────────────────

export type AttributeDef = {
  code: string;
  label: TR;
  unit: TR;
  group: TR;
  groupSort: number;
  widget: "checkbox" | "range_slider" | "switch";
  /** Значення фасета: slug → підпис. */
  options?: { value: string; label: TR }[];
  /** Бакети для range (ADR-007 — фільтруємо по БАКЕТУ, не по числу). */
  buckets?: { value: string; label: TR; from: number; to: number }[];
};

export const attributes: AttributeDef[] = [
  // ⚠️ «Виробник» — рядок ЛИШЕ ДЛЯ ПОКАЗУ в таблиці характеристик, БЕЗ token.
  //   Фільтрація по бренду йде через токен "brand:*" (Brand — окрема FK-модель,
  //   не EAV-атрибут). Якби ми дали цьому рядку ще й token "vyrobnyk:*", у фасетах
  //   з'явилось би ДВІ групи «Виробник» з однаковими значеннями.
  //   На бекенді рівно так само: Brand → filter_tokens, а «Виробник» у specs_json —
  //   денормалізований підпис.
  { code: "vyrobnyk", label: { uk: "Виробник", ru: "Производитель" }, unit: { uk: "", ru: "" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "checkbox" },
  { code: "typ", label: { uk: "Тип", ru: "Тип" }, unit: { uk: "", ru: "" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "checkbox" },
  { code: "kolir", label: { uk: "Колір виробу", ru: "Цвет изделия" }, unit: { uk: "", ru: "" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "checkbox" },
  {
    code: "obiem",
    label: { uk: "Загальний об'єм", ru: "Общий объём" },
    unit: { uk: "л", ru: "л" },
    group: { uk: "Основні", ru: "Основные" },
    groupSort: 1,
    widget: "range_slider",
    buckets: [
      { value: "0-199", label: { uk: "до 200 л", ru: "до 200 л" }, from: 0, to: 199 },
      { value: "200-299", label: { uk: "200–299 л", ru: "200–299 л" }, from: 200, to: 299 },
      { value: "300-399", label: { uk: "300–399 л", ru: "300–399 л" }, from: 300, to: 399 },
      { value: "400-999", label: { uk: "від 400 л", ru: "от 400 л" }, from: 400, to: 999 },
    ],
  },
  { code: "no-frost", label: { uk: "No Frost", ru: "No Frost" }, unit: { uk: "", ru: "" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "switch" },
  { code: "klas-enerhospozhyvannia", label: { uk: "Клас енергоспоживання", ru: "Класс энергопотребления" }, unit: { uk: "", ru: "" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "checkbox" },
  { code: "vysota", label: { uk: "Висота", ru: "Высота" }, unit: { uk: "мм", ru: "мм" }, group: { uk: "Габаритні розміри", ru: "Габаритные размеры" }, groupSort: 2, widget: "range_slider" },
  { code: "shyryna", label: { uk: "Ширина", ru: "Ширина" }, unit: { uk: "мм", ru: "мм" }, group: { uk: "Габаритні розміри", ru: "Габаритные размеры" }, groupSort: 2, widget: "range_slider" },
  { code: "hlybyna", label: { uk: "Глибина", ru: "Глубина" }, unit: { uk: "мм", ru: "мм" }, group: { uk: "Габаритні розміри", ru: "Габаритные размеры" }, groupSort: 2, widget: "range_slider" },
  { code: "vaha", label: { uk: "Вага", ru: "Вес" }, unit: { uk: "кг", ru: "кг" }, group: { uk: "Габаритні розміри", ru: "Габаритные размеры" }, groupSort: 2, widget: "range_slider" },
  { code: "harantiinyi-termin", label: { uk: "Гарантійний термін", ru: "Гарантийный срок" }, unit: { uk: "міс", ru: "мес" }, group: { uk: "Додатково", ru: "Дополнительно" }, groupSort: 3, widget: "checkbox" },
  { code: "krayina", label: { uk: "Країна виробництва", ru: "Страна производства" }, unit: { uk: "", ru: "" }, group: { uk: "Додатково", ru: "Дополнительно" }, groupSort: 3, widget: "checkbox" },
  { code: "diahonal", label: { uk: 'Діагональ екрану, дюймів" (см)', ru: 'Диагональ экрана, дюймов" (см)' }, unit: { uk: "", ru: "" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "checkbox" },
  { code: "potuzhnist", label: { uk: "Потужність", ru: "Мощность" }, unit: { uk: "Вт", ru: "Вт" }, group: { uk: "Основні", ru: "Основные" }, groupSort: 1, widget: "range_slider" },
];

export const attr = (code: string) => attributes.find((a) => a.code === code)!;

// ─────────────────────────────────────────────────────────────────────────────
// Товари
// ─────────────────────────────────────────────────────────────────────────────

export type RawSpec = {
  code: string;
  /** Значення як показувати. Одиниця береться з AttributeDef — НЕ дублюється тут. */
  value: TR;
  /** Числове значення (для порівняння й range-фасетів). */
  num?: number;
  /** Slug значення для filter_tokens (мовонезалежний). */
  token?: string;
};

export type RawProduct = {
  id: number;
  sku: string;
  slug: string;
  name: TR;
  short: TR;
  /** Rich HTML з вбудованими фото (INPUTS §2). */
  description: TR;
  categoryId: number;
  brandId: number;
  countryId: number;
  price: string;
  old_price: string | null;
  availability: Availability;
  order_lead_days: number | null;
  condition: Condition;
  is_featured: boolean;
  installment: number | null;
  warranty_months: number;
  stock_qty: number | null;
  imageCount: number;
  specs: RawSpec[];
  /** Габарити упаковки — вхід калькулятора НП (INPUTS §3.4). */
  pkg: { w: string; width: string; height: string; depth: string };
  /** Членство в групі варіантів. */
  variant?: { groupId: number; label: TR; hex?: string; sort: number };
};

const P = (id: number) => `/images/mock/product-${(id % 8) + 1}.svg`;
void P;

const descFridge: TR = {
  uk: `<h3>Просторий холодильник для великої родини</h3>
<p>Загальний об'єм <strong>331 л</strong> — вистачить на тижневий запас продуктів для родини з чотирьох осіб. Окрема морозильна камера тримає −18 °C навіть у спеку.</p>
<figure><img src="/images/mock/desc-1.svg" alt="Внутрішній простір холодильника" /><figcaption>Полиці з загартованого скла витримують до 25 кг</figcaption></figure>
<h3>Система No Frost</h3>
<p>Крига не наростає — розморожувати вручну не доведеться жодного разу за весь термін служби. Вентилятор рівномірно розподіляє холод по всіх полицях, тож продукти на верхній і нижній полиці зберігаються однаково довго.</p>
<ul><li>Клас енергоспоживання <strong>A++</strong> — близько 240 кВт·год на рік</li><li>Рівень шуму 39 дБ — тихіше за розмову пошепки</li><li>Зона свіжості з окремим контролем вологості</li></ul>
<figure><img src="/images/mock/desc-2.svg" alt="Панель керування" /><figcaption>Сенсорна панель із індикацією температури обох камер</figcaption></figure>
<p>Гарантія від виробника — 12 місяців. Сервісні центри в усіх обласних центрах України.</p>`,
  ru: `<h3>Просторный холодильник для большой семьи</h3>
<p>Общий объём <strong>331 л</strong> — хватит на недельный запас продуктов для семьи из четырёх человек. Отдельная морозильная камера держит −18 °C даже в жару.</p>
<figure><img src="/images/mock/desc-1.svg" alt="Внутреннее пространство холодильника" /><figcaption>Полки из закалённого стекла выдерживают до 25 кг</figcaption></figure>
<h3>Система No Frost</h3>
<p>Наледь не нарастает — размораживать вручную не придётся ни разу за весь срок службы. Вентилятор равномерно распределяет холод по всем полкам.</p>
<ul><li>Класс энергопотребления <strong>A++</strong> — около 240 кВт·ч в год</li><li>Уровень шума 39 дБ — тише разговора шёпотом</li><li>Зона свежести с отдельным контролем влажности</li></ul>
<figure><img src="/images/mock/desc-2.svg" alt="Панель управления" /><figcaption>Сенсорная панель с индикацией температуры обеих камер</figcaption></figure>
<p>Гарантия от производителя — 12 месяцев. Сервисные центры во всех областных центрах Украины.</p>`,
};

const descTv: TR = {
  uk: `<h3>QLED-матриця з квантовими точками</h3>
<p>Мільярд відтінків і 100% об'єму кольору DCI-P3. Яскравість до 1500 ніт — зображення не вигорає навіть при денному світлі з вікна навпроти.</p>
<figure><img src="/images/mock/desc-3.svg" alt="Кут огляду екрана" /><figcaption>Колір не блідне навіть під кутом 178°</figcaption></figure>
<h3>Ігровий режим 144 Гц</h3>
<p>Вхідна затримка 5,8 мс, підтримка VRR і ALLM. HDMI 2.1 на всіх чотирьох портах — можна підключити приставку і ПК одночасно без компромісів.</p>
<ul><li>Процесор із апскейлом до 4K на базі нейромережі</li><li>Dolby Atmos через вбудовані динаміки 2.2, 40 Вт</li><li>Безрамковий корпус — 96% площі передньої панелі це екран</li></ul>`,
  ru: `<h3>QLED-матрица с квантовыми точками</h3>
<p>Миллиард оттенков и 100% объёма цвета DCI-P3. Яркость до 1500 нит — изображение не выгорает даже при дневном свете из окна напротив.</p>
<figure><img src="/images/mock/desc-3.svg" alt="Угол обзора экрана" /><figcaption>Цвет не блёкнет даже под углом 178°</figcaption></figure>
<h3>Игровой режим 144 Гц</h3>
<p>Входная задержка 5,8 мс, поддержка VRR и ALLM. HDMI 2.1 на всех четырёх портах.</p>
<ul><li>Процессор с апскейлом до 4K на базе нейросети</li><li>Dolby Atmos через встроенные динамики 2.2, 40 Вт</li><li>Безрамочный корпус — 96% площади передней панели это экран</li></ul>`,
};

const descMicro: TR = {
  uk: `<h3>Мікрохвильова піч на 20 літрів</h3>
<p>Компактна, але місткої камери вистачає на тарілку діаметром 25 см. Сенсорне керування — вісім автоматичних програм розігріву й розморожування за вагою.</p>
<figure><img src="/images/mock/desc-1.svg" alt="Камера мікрохвильової печі" /><figcaption>Емальоване покриття камери легко миється</figcaption></figure>
<p>Потужність 700 Вт, п'ять рівнів. Таймер до 30 хвилин зі звуковим сигналом.</p>`,
  ru: `<h3>Микроволновая печь на 20 литров</h3>
<p>Компактная, но вместительной камеры хватает на тарелку диаметром 25 см. Сенсорное управление — восемь автоматических программ разогрева и разморозки по весу.</p>
<figure><img src="/images/mock/desc-1.svg" alt="Камера микроволновой печи" /><figcaption>Эмалированное покрытие камеры легко моется</figcaption></figure>
<p>Мощность 700 Вт, пять уровней. Таймер до 30 минут со звуковым сигналом.</p>`,
};

const descGeneric: TR = {
  uk: `<h3>Надійна техніка для щоденних задач</h3>
<p>Модель зібрана на європейському заводі, компоненти — від профільних постачальників. Гарантія від виробника.</p>
<p>Усі параметри, за якими зазвичай обирають, зібрані в блоці «Характеристики» ліворуч: габарити, потужність, клас енергоспоживання.</p>
<figure><img src="/images/mock/desc-2.svg" alt="Загальний вигляд" /><figcaption>Матове покриття не збирає відбитки пальців</figcaption></figure>`,
  ru: `<h3>Надёжная техника для ежедневных задач</h3>
<p>Модель собрана на европейском заводе, компоненты — от профильных поставщиков. Гарантия от производителя.</p>
<p>Все параметры, по которым обычно выбирают, собраны в блоке «Характеристики» слева: габариты, мощность, класс энергопотребления.</p>
<figure><img src="/images/mock/desc-2.svg" alt="Общий вид" /><figcaption>Матовое покрытие не собирает отпечатки пальцев</figcaption></figure>`,
};

/** Група варіантів: 1 = діагональ ТВ (кнопки), 2 = колір холодильника (кружечки). */
export const variantGroups: {
  id: number;
  axisCode: string;
  widget: VariantWidget;
}[] = [
  { id: 1, axisCode: "diahonal", widget: "buttons" },
  { id: 2, axisCode: "kolir", widget: "swatches" },
];

export const rawProducts: RawProduct[] = [
  // ── Холодильники (група варіантів 2: колір, кружечки) ──
  {
    id: 5601, sku: "2400042", slug: "gorenje-nrk6202aw4",
    name: { uk: "Холодильник Gorenje NRK6202AW4", ru: "Холодильник Gorenje NRK6202AW4" },
    short: { uk: "No Frost, 331 л, A++", ru: "No Frost, 331 л, A++" },
    description: descFridge,
    categoryId: 11, brandId: 2, countryId: 5,
    price: "24990.00", old_price: "28490.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 6, warranty_months: 12, stock_qty: 4, imageCount: 5,
    pkg: { w: "61.5", width: "56", height: "171.5", depth: "35" },
    variant: { groupId: 2, label: { uk: "Білий", ru: "Белый" }, hex: "#FFFFFF", sort: 1 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Gorenje", ru: "Gorenje" } },
      { code: "typ", value: { uk: "Двокамерний", ru: "Двухкамерный" }, token: "dvokamernyi" },
      { code: "kolir", value: { uk: "Білий", ru: "Белый" }, token: "bilyi" },
      { code: "obiem", value: { uk: "331", ru: "331" }, num: 331, token: "300-399" },
      { code: "no-frost", value: { uk: "Так", ru: "Да" }, num: 1, token: "1" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A++", ru: "A++" }, token: "a-plus-plus" },
      { code: "vysota", value: { uk: "1635", ru: "1635" }, num: 1635 },
      { code: "shyryna", value: { uk: "490", ru: "490" }, num: 490 },
      { code: "hlybyna", value: { uk: "278", ru: "278" }, num: 278 },
      { code: "vaha", value: { uk: "58", ru: "58" }, num: 58 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Сербія", ru: "Сербия" }, token: "serbiia" },
    ],
  },
  {
    id: 5602, sku: "2400043", slug: "gorenje-nrk6202ai4",
    name: { uk: "Холодильник Gorenje NRK6202AI4", ru: "Холодильник Gorenje NRK6202AI4" },
    short: { uk: "No Frost, 331 л, A++, слонова кістка", ru: "No Frost, 331 л, A++, слоновая кость" },
    description: descFridge,
    categoryId: 11, brandId: 2, countryId: 5,
    price: "25490.00", old_price: null,
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: 6, warranty_months: 12, stock_qty: 2, imageCount: 4,
    pkg: { w: "61.5", width: "56", height: "171.5", depth: "35" },
    variant: { groupId: 2, label: { uk: "Слонова кістка", ru: "Слоновая кость" }, hex: "#F2E8D5", sort: 2 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Gorenje", ru: "Gorenje" } },
      { code: "typ", value: { uk: "Двокамерний", ru: "Двухкамерный" }, token: "dvokamernyi" },
      { code: "kolir", value: { uk: "Слонова кістка", ru: "Слоновая кость" }, token: "slonova-kistka" },
      { code: "obiem", value: { uk: "331", ru: "331" }, num: 331, token: "300-399" },
      { code: "no-frost", value: { uk: "Так", ru: "Да" }, num: 1, token: "1" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A++", ru: "A++" }, token: "a-plus-plus" },
      { code: "vysota", value: { uk: "1635", ru: "1635" }, num: 1635 },
      { code: "shyryna", value: { uk: "490", ru: "490" }, num: 490 },
      { code: "hlybyna", value: { uk: "278", ru: "278" }, num: 278 },
      { code: "vaha", value: { uk: "58", ru: "58" }, num: 58 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Сербія", ru: "Сербия" }, token: "serbiia" },
    ],
  },
  {
    id: 5603, sku: "2400044", slug: "gorenje-nrk6202ab4",
    name: { uk: "Холодильник Gorenje NRK6202AB4", ru: "Холодильник Gorenje NRK6202AB4" },
    short: { uk: "No Frost, 331 л, A++, чорний", ru: "No Frost, 331 л, A++, чёрный" },
    description: descFridge,
    categoryId: 11, brandId: 2, countryId: 5,
    price: "26490.00", old_price: null,
    // Варіант ІСНУЄ, але його немає — свічер має показати його перекресленим, а не сховати.
    availability: "out_of_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: null, warranty_months: 12, stock_qty: 0, imageCount: 3,
    pkg: { w: "61.5", width: "56", height: "171.5", depth: "35" },
    variant: { groupId: 2, label: { uk: "Чорний", ru: "Чёрный" }, hex: "#1A1A1A", sort: 3 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Gorenje", ru: "Gorenje" } },
      { code: "typ", value: { uk: "Двокамерний", ru: "Двухкамерный" }, token: "dvokamernyi" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "obiem", value: { uk: "331", ru: "331" }, num: 331, token: "300-399" },
      { code: "no-frost", value: { uk: "Так", ru: "Да" }, num: 1, token: "1" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A++", ru: "A++" }, token: "a-plus-plus" },
      { code: "vysota", value: { uk: "1635", ru: "1635" }, num: 1635 },
      { code: "shyryna", value: { uk: "490", ru: "490" }, num: 490 },
      { code: "hlybyna", value: { uk: "278", ru: "278" }, num: 278 },
      { code: "vaha", value: { uk: "58", ru: "58" }, num: 58 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Сербія", ru: "Сербия" }, token: "serbiia" },
    ],
  },
  {
    id: 5610, sku: "2400051", slug: "bosch-kgn39vleb",
    name: { uk: "Холодильник Bosch KGN39VLEB", ru: "Холодильник Bosch KGN39VLEB" },
    short: { uk: "No Frost, 366 л, A++", ru: "No Frost, 366 л, A++" },
    description: descFridge,
    categoryId: 11, brandId: 1, countryId: 1,
    price: "38900.00", old_price: "42400.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 12, warranty_months: 24, stock_qty: 7, imageCount: 6,
    pkg: { w: "72.0", width: "65", height: "210", depth: "70" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Bosch", ru: "Bosch" } },
      { code: "typ", value: { uk: "Двокамерний", ru: "Двухкамерный" }, token: "dvokamernyi" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "obiem", value: { uk: "366", ru: "366" }, num: 366, token: "300-399" },
      { code: "no-frost", value: { uk: "Так", ru: "Да" }, num: 1, token: "1" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A++", ru: "A++" }, token: "a-plus-plus" },
      { code: "vysota", value: { uk: "2030", ru: "2030" }, num: 2030 },
      { code: "shyryna", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "hlybyna", value: { uk: "660", ru: "660" }, num: 660 },
      { code: "vaha", value: { uk: "68", ru: "68" }, num: 68 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Німеччина", ru: "Германия" }, token: "nimechchyna" },
    ],
  },
  {
    id: 5611, sku: "2400052", slug: "samsung-rb34t672fsa",
    name: { uk: "Холодильник Samsung RB34T672FSA", ru: "Холодильник Samsung RB34T672FSA" },
    short: { uk: "No Frost, 344 л, інверторний компресор", ru: "No Frost, 344 л, инверторный компрессор" },
    description: descFridge,
    categoryId: 11, brandId: 4, countryId: 4,
    price: "31200.00", old_price: null,
    availability: "on_order", order_lead_days: 5, condition: 0, is_featured: false,
    installment: 6, warranty_months: 12, stock_qty: null, imageCount: 4,
    pkg: { w: "70.0", width: "63", height: "192", depth: "70" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "Двокамерний", ru: "Двухкамерный" }, token: "dvokamernyi" },
      { code: "kolir", value: { uk: "Сріблястий", ru: "Серебристый" }, token: "sriblyastyi" },
      { code: "obiem", value: { uk: "344", ru: "344" }, num: 344, token: "300-399" },
      { code: "no-frost", value: { uk: "Так", ru: "Да" }, num: 1, token: "1" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A+", ru: "A+" }, token: "a-plus" },
      { code: "vysota", value: { uk: "1855", ru: "1855" }, num: 1855 },
      { code: "shyryna", value: { uk: "595", ru: "595" }, num: 595 },
      { code: "hlybyna", value: { uk: "650", ru: "650" }, num: 650 },
      { code: "vaha", value: { uk: "64", ru: "64" }, num: 64 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Польща", ru: "Польша" }, token: "polshcha" },
    ],
  },
  {
    id: 5612, sku: "2400053", slug: "electrolux-lnt5me36u1",
    name: { uk: "Холодильник Electrolux LNT5ME36U1", ru: "Холодильник Electrolux LNT5ME36U1" },
    short: { uk: "No Frost, 280 л, уцінка", ru: "No Frost, 280 л, уценка" },
    description: descGeneric,
    categoryId: 11, brandId: 6, countryId: 4,
    price: "19900.00", old_price: "26900.00",
    availability: "in_stock", order_lead_days: null, condition: 2, is_featured: false,
    installment: 6, warranty_months: 12, stock_qty: 1, imageCount: 3,
    pkg: { w: "58.0", width: "60", height: "180", depth: "65" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Electrolux", ru: "Electrolux" } },
      { code: "typ", value: { uk: "Двокамерний", ru: "Двухкамерный" }, token: "dvokamernyi" },
      { code: "kolir", value: { uk: "Білий", ru: "Белый" }, token: "bilyi" },
      { code: "obiem", value: { uk: "280", ru: "280" }, num: 280, token: "200-299" },
      { code: "no-frost", value: { uk: "Так", ru: "Да" }, num: 1, token: "1" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A+", ru: "A+" }, token: "a-plus" },
      { code: "vysota", value: { uk: "1750", ru: "1750" }, num: 1750 },
      { code: "shyryna", value: { uk: "545", ru: "545" }, num: 545 },
      { code: "hlybyna", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "vaha", value: { uk: "55", ru: "55" }, num: 55 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Польща", ru: "Польша" }, token: "polshcha" },
    ],
  },

  // ── Телевізори (група варіантів 1: діагональ, кнопки) ──
  {
    id: 5691, sku: "56951", slug: "samsung-qled-q60d-50",
    name: { uk: 'Телевізор Samsung QLED Q60D 50"', ru: 'Телевизор Samsung QLED Q60D 50"' },
    short: { uk: "4K, 144 Гц, Tizen", ru: "4K, 144 Гц, Tizen" },
    description: descTv,
    categoryId: 31, brandId: 4, countryId: 3,
    price: "27990.00", old_price: "31990.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 12, warranty_months: 24, stock_qty: 5, imageCount: 5,
    pkg: { w: "18.5", width: "125", height: "78", depth: "16" },
    variant: { groupId: 1, label: { uk: '50" (127 см)', ru: '50" (127 см)' }, sort: 1 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "QLED", ru: "QLED" }, token: "qled" },
      { code: "diahonal", value: { uk: '50" (127 см)', ru: '50" (127 см)' }, num: 50, token: "50" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "vysota", value: { uk: "715", ru: "715" }, num: 715 },
      { code: "shyryna", value: { uk: "1116", ru: "1116" }, num: 1116 },
      { code: "hlybyna", value: { uk: "58", ru: "58" }, num: 58 },
      { code: "vaha", value: { uk: "13", ru: "13" }, num: 13 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },
  {
    id: 5692, sku: "56952", slug: "samsung-qled-q60d-55",
    name: { uk: 'Телевізор Samsung QLED Q60D 55"', ru: 'Телевизор Samsung QLED Q60D 55"' },
    short: { uk: "4K, 144 Гц, Tizen", ru: "4K, 144 Гц, Tizen" },
    description: descTv,
    categoryId: 31, brandId: 4, countryId: 3,
    price: "32490.00", old_price: null,
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: 12, warranty_months: 24, stock_qty: 3, imageCount: 4,
    pkg: { w: "21.0", width: "137", height: "85", depth: "17" },
    variant: { groupId: 1, label: { uk: '55" (139,7 см)', ru: '55" (139,7 см)' }, sort: 2 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "QLED", ru: "QLED" }, token: "qled" },
      { code: "diahonal", value: { uk: '55" (139,7 см)', ru: '55" (139,7 см)' }, num: 55, token: "55" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "vysota", value: { uk: "785", ru: "785" }, num: 785 },
      { code: "shyryna", value: { uk: "1230", ru: "1230" }, num: 1230 },
      { code: "hlybyna", value: { uk: "58", ru: "58" }, num: 58 },
      { code: "vaha", value: { uk: "16", ru: "16" }, num: 16 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },
  {
    id: 5693, sku: "56953", slug: "samsung-qled-q60d-65",
    name: { uk: 'Телевізор Samsung QLED Q60D 65"', ru: 'Телевизор Samsung QLED Q60D 65"' },
    short: { uk: "4K, 144 Гц, Tizen", ru: "4K, 144 Гц, Tizen" },
    description: descTv,
    categoryId: 31, brandId: 4, countryId: 3,
    price: "44900.00", old_price: "49900.00",
    availability: "on_order", order_lead_days: 3, condition: 0, is_featured: true,
    installment: 12, warranty_months: 24, stock_qty: null, imageCount: 4,
    pkg: { w: "28.0", width: "160", height: "98", depth: "19" },
    variant: { groupId: 1, label: { uk: '65" (163,9 см)', ru: '65" (163,9 см)' }, sort: 3 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "QLED", ru: "QLED" }, token: "qled" },
      { code: "diahonal", value: { uk: '65" (163,9 см)', ru: '65" (163,9 см)' }, num: 65, token: "65" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "vysota", value: { uk: "925", ru: "925" }, num: 925 },
      { code: "shyryna", value: { uk: "1450", ru: "1450" }, num: 1450 },
      { code: "hlybyna", value: { uk: "62", ru: "62" }, num: 62 },
      { code: "vaha", value: { uk: "23", ru: "23" }, num: 23 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },
  {
    id: 5694, sku: "56954", slug: "samsung-qled-q60d-75",
    name: { uk: 'Телевізор Samsung QLED Q60D 75"', ru: 'Телевизор Samsung QLED Q60D 75"' },
    short: { uk: "4K, 144 Гц, Tizen", ru: "4K, 144 Гц, Tizen" },
    description: descTv,
    categoryId: 31, brandId: 4, countryId: 3,
    price: "68900.00", old_price: null,
    availability: "out_of_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: null, warranty_months: 24, stock_qty: 0, imageCount: 3,
    pkg: { w: "38.0", width: "182", height: "112", depth: "22" },
    variant: { groupId: 1, label: { uk: '75" (189,3 см)', ru: '75" (189,3 см)' }, sort: 4 },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "QLED", ru: "QLED" }, token: "qled" },
      { code: "diahonal", value: { uk: '75" (189,3 см)', ru: '75" (189,3 см)' }, num: 75, token: "75" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "vysota", value: { uk: "1055", ru: "1055" }, num: 1055 },
      { code: "shyryna", value: { uk: "1670", ru: "1670" }, num: 1670 },
      { code: "hlybyna", value: { uk: "65", ru: "65" }, num: 65 },
      { code: "vaha", value: { uk: "32", ru: "32" }, num: 32 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },
  {
    id: 5695, sku: "56960", slug: "lg-oled-c4-55",
    name: { uk: 'Телевізор LG OLED C4 55"', ru: 'Телевизор LG OLED C4 55"' },
    short: { uk: "OLED evo, 4K, 144 Гц, webOS", ru: "OLED evo, 4K, 144 Гц, webOS" },
    description: descTv,
    categoryId: 31, brandId: 3, countryId: 3,
    price: "54900.00", old_price: "62900.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 12, warranty_months: 24, stock_qty: 2, imageCount: 5,
    pkg: { w: "22.0", width: "137", height: "85", depth: "17" },
    specs: [
      { code: "vyrobnyk", value: { uk: "LG", ru: "LG" } },
      { code: "typ", value: { uk: "OLED", ru: "OLED" }, token: "oled" },
      { code: "diahonal", value: { uk: '55" (139,7 см)', ru: '55" (139,7 см)' }, num: 55, token: "55" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "vysota", value: { uk: "710", ru: "710" }, num: 710 },
      { code: "shyryna", value: { uk: "1226", ru: "1226" }, num: 1226 },
      { code: "hlybyna", value: { uk: "45", ru: "45" }, num: 45 },
      { code: "vaha", value: { uk: "14", ru: "14" }, num: 14 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },

  // ── Мікрохвильові печі ──
  {
    id: 5721, sku: "31200", slug: "lg-ms2042db",
    name: { uk: "Мікрохвильова піч LG MS2042DB", ru: "Микроволновая печь LG MS2042DB" },
    short: { uk: "20 л, 700 Вт, сенсорне керування", ru: "20 л, 700 Вт, сенсорное управление" },
    description: descMicro,
    categoryId: 21, brandId: 3, countryId: 3,
    price: "718.00", old_price: "785.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 6, warranty_months: 12, stock_qty: 12, imageCount: 4,
    pkg: { w: "12.4", width: "50", height: "32", depth: "42" },
    specs: [
      { code: "vyrobnyk", value: { uk: "LG", ru: "LG" } },
      { code: "typ", value: { uk: "Соло", ru: "Соло" }, token: "solo" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "obiem", value: { uk: "20", ru: "20" }, num: 20, token: "0-199" },
      { code: "potuzhnist", value: { uk: "700", ru: "700" }, num: 700 },
      { code: "vysota", value: { uk: "284", ru: "284" }, num: 284 },
      { code: "shyryna", value: { uk: "455", ru: "455" }, num: 455 },
      { code: "hlybyna", value: { uk: "358", ru: "358" }, num: 358 },
      { code: "vaha", value: { uk: "11", ru: "11" }, num: 11 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },
  {
    id: 5722, sku: "31201", slug: "samsung-me83xr",
    name: { uk: "Мікрохвильова піч Samsung ME83XR", ru: "Микроволновая печь Samsung ME83XR" },
    short: { uk: "23 л, 800 Вт", ru: "23 л, 800 Вт" },
    description: descMicro,
    categoryId: 21, brandId: 4, countryId: 4,
    price: "3490.00", old_price: null,
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: 6, warranty_months: 12, stock_qty: 8, imageCount: 3,
    pkg: { w: "13.0", width: "52", height: "34", depth: "44" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "Соло", ru: "Соло" }, token: "solo" },
      { code: "kolir", value: { uk: "Сріблястий", ru: "Серебристый" }, token: "sriblyastyi" },
      { code: "obiem", value: { uk: "23", ru: "23" }, num: 23, token: "0-199" },
      { code: "potuzhnist", value: { uk: "800", ru: "800" }, num: 800 },
      { code: "vysota", value: { uk: "275", ru: "275" }, num: 275 },
      { code: "shyryna", value: { uk: "489", ru: "489" }, num: 489 },
      { code: "hlybyna", value: { uk: "374", ru: "374" }, num: 374 },
      { code: "vaha", value: { uk: "12", ru: "12" }, num: 12 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Польща", ru: "Польша" }, token: "polshcha" },
    ],
  },

  // ── Пральні машини ──
  {
    id: 5801, sku: "41100", slug: "bosch-wan28263",
    name: { uk: "Пральна машина Bosch WAN28263", ru: "Стиральная машина Bosch WAN28263" },
    short: { uk: "8 кг, 1400 об/хв, EcoSilence Drive", ru: "8 кг, 1400 об/мин, EcoSilence Drive" },
    description: descGeneric,
    categoryId: 12, brandId: 1, countryId: 1,
    price: "21900.00", old_price: "24500.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 12, warranty_months: 24, stock_qty: 6, imageCount: 4,
    pkg: { w: "72.0", width: "65", height: "90", depth: "68" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Bosch", ru: "Bosch" } },
      { code: "typ", value: { uk: "Фронтальна", ru: "Фронтальная" }, token: "frontalna" },
      { code: "kolir", value: { uk: "Білий", ru: "Белый" }, token: "bilyi" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A+++", ru: "A+++" }, token: "a-plus-plus-plus" },
      { code: "vysota", value: { uk: "848", ru: "848" }, num: 848 },
      { code: "shyryna", value: { uk: "598", ru: "598" }, num: 598 },
      { code: "hlybyna", value: { uk: "590", ru: "590" }, num: 590 },
      { code: "vaha", value: { uk: "70", ru: "70" }, num: 70 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Німеччина", ru: "Германия" }, token: "nimechchyna" },
    ],
  },
  {
    id: 5802, sku: "41101", slug: "gorenje-wnei84bs",
    name: { uk: "Пральна машина Gorenje WNEI84BS", ru: "Стиральная машина Gorenje WNEI84BS" },
    short: { uk: "8 кг, 1400 об/хв, інверторний мотор", ru: "8 кг, 1400 об/мин, инверторный мотор" },
    description: descGeneric,
    categoryId: 12, brandId: 2, countryId: 2,
    price: "15900.00", old_price: null,
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: 6, warranty_months: 12, stock_qty: 9, imageCount: 3,
    pkg: { w: "68.0", width: "64", height: "89", depth: "66" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Gorenje", ru: "Gorenje" } },
      { code: "typ", value: { uk: "Фронтальна", ru: "Фронтальная" }, token: "frontalna" },
      { code: "kolir", value: { uk: "Білий", ru: "Белый" }, token: "bilyi" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A++", ru: "A++" }, token: "a-plus-plus" },
      { code: "vysota", value: { uk: "850", ru: "850" }, num: 850 },
      { code: "shyryna", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "hlybyna", value: { uk: "545", ru: "545" }, num: 545 },
      { code: "vaha", value: { uk: "66", ru: "66" }, num: 66 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Словенія", ru: "Словения" }, token: "sloveniia" },
    ],
  },
  {
    id: 5803, sku: "41102", slug: "lg-f2wv3s8s3e",
    name: { uk: "Пральна машина LG F2WV3S8S3E", ru: "Стиральная машина LG F2WV3S8S3E" },
    short: { uk: "8,5 кг, AI DD, пара", ru: "8,5 кг, AI DD, пар" },
    description: descGeneric,
    categoryId: 12, brandId: 3, countryId: 4,
    price: "26400.00", old_price: "29900.00",
    availability: "on_order", order_lead_days: 4, condition: 0, is_featured: false,
    installment: 12, warranty_months: 24, stock_qty: null, imageCount: 4,
    pkg: { w: "66.0", width: "66", height: "93", depth: "60" },
    specs: [
      { code: "vyrobnyk", value: { uk: "LG", ru: "LG" } },
      { code: "typ", value: { uk: "Фронтальна", ru: "Фронтальная" }, token: "frontalna" },
      { code: "kolir", value: { uk: "Сріблястий", ru: "Серебристый" }, token: "sriblyastyi" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A+++", ru: "A+++" }, token: "a-plus-plus-plus" },
      { code: "vysota", value: { uk: "850", ru: "850" }, num: 850 },
      { code: "shyryna", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "hlybyna", value: { uk: "475", ru: "475" }, num: 475 },
      { code: "vaha", value: { uk: "62", ru: "62" }, num: 62 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Польща", ru: "Польша" }, token: "polshcha" },
    ],
  },

  // ── Пилососи ──
  {
    id: 5901, sku: "52300", slug: "samsung-jet-bot-ai",
    name: { uk: "Робот-пилосос Samsung Jet Bot AI+", ru: "Робот-пылесос Samsung Jet Bot AI+" },
    short: { uk: "LiDAR, станція самоочищення", ru: "LiDAR, станция самоочистки" },
    description: descGeneric,
    categoryId: 22, brandId: 4, countryId: 3,
    price: "34900.00", old_price: "39900.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 12, warranty_months: 12, stock_qty: 3, imageCount: 5,
    pkg: { w: "12.0", width: "45", height: "45", depth: "40" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Samsung", ru: "Samsung" } },
      { code: "typ", value: { uk: "Робот", ru: "Робот" }, token: "robot" },
      { code: "kolir", value: { uk: "Білий", ru: "Белый" }, token: "bilyi" },
      { code: "potuzhnist", value: { uk: "170", ru: "170" }, num: 170 },
      { code: "vysota", value: { uk: "97", ru: "97" }, num: 97 },
      { code: "shyryna", value: { uk: "350", ru: "350" }, num: 350 },
      { code: "hlybyna", value: { uk: "350", ru: "350" }, num: 350 },
      { code: "vaha", value: { uk: "5", ru: "5" }, num: 5 },
      { code: "harantiinyi-termin", value: { uk: "12", ru: "12" }, num: 12, token: "12" },
      { code: "krayina", value: { uk: "Південна Корея", ru: "Южная Корея" }, token: "pivdenna-koreia" },
    ],
  },
  {
    id: 5902, sku: "52301", slug: "bosch-bgls4hyg2",
    name: { uk: "Пилосос Bosch BGLS4HYG2", ru: "Пылесос Bosch BGLS4HYG2" },
    short: { uk: "Мішковий, 600 Вт, HEPA H13", ru: "Мешковый, 600 Вт, HEPA H13" },
    description: descGeneric,
    categoryId: 22, brandId: 1, countryId: 1,
    price: "6490.00", old_price: null,
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: null, warranty_months: 24, stock_qty: 14, imageCount: 3,
    pkg: { w: "6.5", width: "45", height: "30", depth: "35" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Bosch", ru: "Bosch" } },
      { code: "typ", value: { uk: "Мішковий", ru: "Мешковый" }, token: "mishkovyi" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "potuzhnist", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "vysota", value: { uk: "255", ru: "255" }, num: 255 },
      { code: "shyryna", value: { uk: "300", ru: "300" }, num: 300 },
      { code: "hlybyna", value: { uk: "460", ru: "460" }, num: 460 },
      { code: "vaha", value: { uk: "6", ru: "6" }, num: 6 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Німеччина", ru: "Германия" }, token: "nimechchyna" },
    ],
  },

  // ── Посудомийні ──
  {
    id: 5951, sku: "61100", slug: "siemens-sn23hi60ce",
    name: { uk: "Посудомийна машина Siemens SN23HI60CE", ru: "Посудомоечная машина Siemens SN23HI60CE" },
    short: { uk: "14 комплектів, 60 см", ru: "14 комплектов, 60 см" },
    description: descGeneric,
    categoryId: 13, brandId: 5, countryId: 1,
    price: "29900.00", old_price: "33500.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: false,
    installment: 12, warranty_months: 24, stock_qty: 4, imageCount: 4,
    pkg: { w: "48.0", width: "65", height: "90", depth: "68" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Siemens", ru: "Siemens" } },
      { code: "typ", value: { uk: "Окремостояча", ru: "Отдельностоящая" }, token: "okremostoyacha" },
      { code: "kolir", value: { uk: "Сріблястий", ru: "Серебристый" }, token: "sriblyastyi" },
      { code: "klas-enerhospozhyvannia", value: { uk: "A++", ru: "A++" }, token: "a-plus-plus" },
      { code: "vysota", value: { uk: "845", ru: "845" }, num: 845 },
      { code: "shyryna", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "hlybyna", value: { uk: "600", ru: "600" }, num: 600 },
      { code: "vaha", value: { uk: "45", ru: "45" }, num: 45 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Німеччина", ru: "Германия" }, token: "nimechchyna" },
    ],
  },

  // ── Кавомашини ──
  {
    id: 5971, sku: "72100", slug: "siemens-eq500-ti513",
    name: { uk: "Кавомашина Siemens EQ.500 TI513", ru: "Кофемашина Siemens EQ.500 TI513" },
    short: { uk: "Автоматична, керамічні жорна", ru: "Автоматическая, керамические жернова" },
    description: descGeneric,
    categoryId: 23, brandId: 5, countryId: 1,
    price: "23900.00", old_price: "27400.00",
    availability: "in_stock", order_lead_days: null, condition: 0, is_featured: true,
    installment: 12, warranty_months: 24, stock_qty: 5, imageCount: 4,
    pkg: { w: "9.8", width: "38", height: "42", depth: "48" },
    specs: [
      { code: "vyrobnyk", value: { uk: "Siemens", ru: "Siemens" } },
      { code: "typ", value: { uk: "Автоматична", ru: "Автоматическая" }, token: "avtomatychna" },
      { code: "kolir", value: { uk: "Чорний", ru: "Чёрный" }, token: "chornyi" },
      { code: "potuzhnist", value: { uk: "1500", ru: "1500" }, num: 1500 },
      { code: "vysota", value: { uk: "385", ru: "385" }, num: 385 },
      { code: "shyryna", value: { uk: "300", ru: "300" }, num: 300 },
      { code: "hlybyna", value: { uk: "470", ru: "470" }, num: 470 },
      { code: "vaha", value: { uk: "9", ru: "9" }, num: 9 },
      { code: "harantiinyi-termin", value: { uk: "24", ru: "24" }, num: 24, token: "24" },
      { code: "krayina", value: { uk: "Німеччина", ru: "Германия" }, token: "nimechchyna" },
    ],
  },
];

export function productsCountIn(categoryId: number): number {
  const childIds = rawCategories.filter((c) => c.parent_id === categoryId).map((c) => c.id);
  const ids = new Set([categoryId, ...childIds]);
  return rawProducts.filter((p) => ids.has(p.categoryId)).length;
}

/** Рекурсивно: id категорії + усі нащадки. Каталог кореня показує все з гілки. */
export function categoryWithDescendants(categoryId: number): Set<number> {
  const result = new Set([categoryId]);
  let added = true;
  while (added) {
    added = false;
    for (const c of rawCategories) {
      if (c.parent_id !== null && result.has(c.parent_id) && !result.has(c.id)) {
        result.add(c.id);
        added = true;
      }
    }
  }
  return result;
}

/** Збирає specs_json так, як його будує rebuild_product_denorm на бекенді. */
export function buildSpecs(p: RawProduct, lang: ApiLang): SpecRow[] {
  return p.specs
    .map((s, index): SpecRow => {
      const a = attr(s.code);
      return {
        g: tr(a.group, lang),
        gs: a.groupSort,
        code: a.code,
        n: tr(a.label, lang), // назва БЕЗ одиниці
        u: tr(a.unit, lang), // одиниця окремо — клеїться до ЗНАЧЕННЯ на рендері
        v: tr(s.value, lang),
        vn: s.num ?? null,
        s: index,
      };
    })
    .sort((a, b) => a.gs - b.gs || a.s - b.s);
}

/** filter_tokens — мовонезалежні (тільки slug-и). Так само, як на бекенді. */
export function buildTokens(p: RawProduct): string[] {
  const tokens: string[] = [
    `brand:${brands.find((b) => b.id === p.brandId)!.slug}`,
    `avail:${p.availability}`,
    `cond:${p.condition}`,
  ];
  if (p.installment) tokens.push("installment:1");
  for (const s of p.specs) {
    if (s.token) tokens.push(`${s.code}:${s.token}`);
  }
  return tokens;
}

export function productImages(p: RawProduct) {
  return Array.from({ length: p.imageCount }, (_, i) => ({
    id: p.id * 100 + i,
    url: `/images/mock/product-${((p.id + i) % 8) + 1}.svg`,
    alt: p.name.uk,
    width: 800,
    height: 800,
    sort_order: i,
  }));
}

// ─────────────────────────────────────────────────────────────────────────────
// CMS
// ─────────────────────────────────────────────────────────────────────────────

export const rawBanners: { id: number; title: TR; subtitle: TR; link: string; cta: TR; image: string }[] = [
  {
    id: 1,
    title: { uk: "Весняні знижки до −30%", ru: "Весенние скидки до −30%" },
    subtitle: { uk: "Холодильники, пральні, посудомийні — до кінця місяця", ru: "Холодильники, стиральные, посудомоечные — до конца месяца" },
    link: "/catalog/velyka-pobutova-tehnika",
    cta: { uk: "До каталогу", ru: "В каталог" },
    image: "/images/mock/banner-1.svg",
  },
  {
    id: 2,
    title: { uk: "Оплата частинами без переплат", ru: "Оплата частями без переплат" },
    subtitle: { uk: "До 12 платежів на техніку від 3 000 ₴", ru: "До 12 платежей на технику от 3 000 ₴" },
    link: "/page/oplata-chastynamy",
    cta: { uk: "Умови", ru: "Условия" },
    image: "/images/mock/banner-2.svg",
  },
  {
    id: 3,
    title: { uk: "Телевізори Samsung QLED", ru: "Телевизоры Samsung QLED" },
    subtitle: { uk: "Від 50\" до 85\" — усі діагоналі в наявності", ru: "От 50\" до 85\" — все диагонали в наличии" },
    link: "/catalog/tv-audio-video/televizory",
    cta: { uk: "Обрати", ru: "Выбрать" },
    image: "/images/mock/banner-3.svg",
  },
];

export const rawNews: { id: number; slug: string; title: TR; excerpt: TR; body: TR; published_at: string }[] = [
  {
    id: 1,
    slug: "yak-obraty-holodylnyk",
    title: { uk: "Як обрати холодильник: 5 параметрів, що справді важать", ru: "Как выбрать холодильник: 5 параметров, которые действительно важны" },
    excerpt: { uk: "Об'єм, No Frost, клас енергоспоживання, рівень шуму й габарити ніші — розбираємо на прикладах.", ru: "Объём, No Frost, класс энергопотребления, уровень шума и габариты ниши — разбираем на примерах." },
    body: {
      uk: `<h2>Об'єм</h2><p>Рахуйте приблизно 120 л на першу людину і по 60 л на кожну наступну. Родині з чотирьох осіб вистачить 300–350 л.</p><h2>No Frost</h2><p>Головна перевага — не треба розморожувати. Мінус: сухіше повітря в камері, овочі краще тримати в контейнерах.</p><h2>Клас енергоспоживання</h2><p>Різниця між A+ і A+++ — приблизно 1 500 ₴ на рік. За 10 років служби це 15 000 ₴.</p><h2>Габарити ніші</h2><p>Міряйте не тільки ширину, а й глибину з ручкою і кут відкриття дверцят.</p>`,
      ru: `<h2>Объём</h2><p>Считайте примерно 120 л на первого человека и по 60 л на каждого следующего. Семье из четырёх человек хватит 300–350 л.</p><h2>No Frost</h2><p>Главное преимущество — не надо размораживать. Минус: суше воздух в камере, овощи лучше держать в контейнерах.</p><h2>Класс энергопотребления</h2><p>Разница между A+ и A+++ — примерно 1 500 ₴ в год.</p><h2>Габариты ниши</h2><p>Меряйте не только ширину, но и глубину с ручкой и угол открытия дверцы.</p>`,
    },
    published_at: "2026-06-28T10:00:00Z",
  },
  {
    id: 2,
    slug: "oplata-chastynamy-yak-tse-pratsyuye",
    title: { uk: "Оплата частинами: як це працює", ru: "Оплата частями: как это работает" },
    excerpt: { uk: "До 12 платежів без переплат. Потрібен лише паспорт і картка — оформлення онлайн за 5 хвилин.", ru: "До 12 платежей без переплат. Нужен только паспорт и карта — оформление онлайн за 5 минут." },
    body: {
      uk: `<p>Оформлюєте замовлення як звичайно, у способах оплати обираєте «Оплата частинами». Далі — переадресація в банк, підтвердження по SMS, і техніка їде до вас.</p><p>Переплат немає: сума платежів дорівнює ціні товару на сайті.</p>`,
      ru: `<p>Оформляете заказ как обычно, в способах оплаты выбираете «Оплата частями». Далее — переадресация в банк, подтверждение по SMS.</p><p>Переплат нет: сумма платежей равна цене товара на сайте.</p>`,
    },
    published_at: "2026-06-15T09:00:00Z",
  },
  {
    id: 3,
    slug: "novi-nadhodzhennya-lypen",
    title: { uk: "Нові надходження: липень", ru: "Новые поступления: июль" },
    excerpt: { uk: "Поповнили склад холодильниками Bosch і телевізорами LG OLED C4.", ru: "Пополнили склад холодильниками Bosch и телевизорами LG OLED C4." },
    body: {
      uk: `<p>На складі з'явилися нові моделі. Усі — з гарантією від виробника, у наявності в Ужгороді.</p>`,
      ru: `<p>На складе появились новые модели. Все — с гарантией от производителя, в наличии в Ужгороде.</p>`,
    },
    published_at: "2026-07-05T12:00:00Z",
  },
];

export const rawPages: { id: number; slug: string; title: TR; body: TR }[] = [
  {
    id: 1,
    slug: "dostavka-i-oplata",
    title: { uk: "Доставка і оплата", ru: "Доставка и оплата" },
    body: {
      uk: `<h2>Доставка</h2><ul><li><strong>Нова Пошта</strong> — відділення, поштомат або кур'єр. За тарифами перевізника.</li><li><strong>Meest, Delivery</strong> — за домовленістю.</li><li><strong>Самовивіз</strong> — м. Ужгород, безкоштовно.</li><li><strong>Кур'єр по Ужгороду</strong> — 150 ₴.</li></ul><h2>Оплата</h2><ul><li>Накладений платіж при отриманні</li><li>Повна передоплата на реквізити</li><li>Онлайн-оплата картою (LiqPay)</li><li>Оплата частинами — до 12 платежів без переплат</li></ul>`,
      ru: `<h2>Доставка</h2><ul><li><strong>Новая Почта</strong> — отделение, почтомат или курьер. По тарифам перевозчика.</li><li><strong>Meest, Delivery</strong> — по договорённости.</li><li><strong>Самовывоз</strong> — г. Ужгород, бесплатно.</li><li><strong>Курьер по Ужгороду</strong> — 150 ₴.</li></ul><h2>Оплата</h2><ul><li>Наложенный платёж при получении</li><li>Полная предоплата на реквизиты</li><li>Онлайн-оплата картой (LiqPay)</li><li>Оплата частями — до 12 платежей без переплат</li></ul>`,
    },
  },
  {
    id: 2,
    slug: "harantiya",
    title: { uk: "Гарантія і сервіс", ru: "Гарантия и сервис" },
    body: {
      uk: `<h2>Гарантія від виробника</h2><p>На всю техніку діє гарантія виробника. Термін вказаний у характеристиках кожного товару (поле «Гарантійний термін»).</p><h2>Що робити при поломці</h2><p>Зателефонуйте нам — підкажемо найближчий авторизований сервісний центр.</p>`,
      ru: `<h2>Гарантия от производителя</h2><p>На всю технику действует гарантия производителя. Срок указан в характеристиках каждого товара (поле «Гарантийный срок»).</p><h2>Что делать при поломке</h2><p>Позвоните нам — подскажем ближайший авторизованный сервисный центр.</p>`,
    },
  },
  {
    id: 3,
    slug: "povernennya",
    title: { uk: "Обмін і повернення", ru: "Обмен и возврат" },
    body: {
      uk: `<p>Товар належної якості можна повернути протягом <strong>14 днів</strong> з моменту купівлі, якщо він не був у використанні та збережено товарний вигляд, пломби й упаковку.</p><p>Товар неналежної якості — повернення або обмін у межах гарантійного строку.</p>`,
      ru: `<p>Товар надлежащего качества можно вернуть в течение <strong>14 дней</strong> с момента покупки, если он не был в использовании и сохранён товарный вид, пломбы и упаковка.</p><p>Товар ненадлежащего качества — возврат или обмен в рамках гарантийного срока.</p>`,
    },
  },
  {
    id: 4,
    slug: "oplata-chastynamy",
    title: { uk: "Оплата частинами", ru: "Оплата частями" },
    body: {
      uk: `<p>До <strong>12 платежів без переплат</strong> на техніку від 3 000 ₴. Оформлення онлайн під час оформлення замовлення.</p><p>Бейдж «оплата частинами» на картці товару означає, що конкретно ця модель доступна в розстрочку.</p>`,
      ru: `<p>До <strong>12 платежей без переплат</strong> на технику от 3 000 ₴. Оформление онлайн во время оформления заказа.</p><p>Бейдж «оплата частями» на карточке товара означает, что конкретно эта модель доступна в рассрочку.</p>`,
    },
  },
  {
    id: 5,
    slug: "pro-nas",
    title: { uk: "Про магазин", ru: "О магазине" },
    body: {
      uk: `<p>Complex — магазин побутової техніки в Ужгороді. Працюємо з офіційними постачальниками, уся техніка з гарантією від виробника.</p><p>Самовивіз у нас, доставка — по всій Україні.</p>`,
      ru: `<p>Complex — магазин бытовой техники в Ужгороде. Работаем с официальными поставщиками, вся техника с гарантией от производителя.</p><p>Самовывоз у нас, доставка — по всей Украине.</p>`,
    },
  },
];

export const rawMenu: { id: number; block: "info" | "buyers"; slug: string; title: TR }[] = [
  { id: 1, block: "info", slug: "pro-nas", title: { uk: "Про магазин", ru: "О магазине" } },
  { id: 2, block: "info", slug: "harantiya", title: { uk: "Гарантія і сервіс", ru: "Гарантия и сервис" } },
  { id: 3, block: "buyers", slug: "dostavka-i-oplata", title: { uk: "Доставка і оплата", ru: "Доставка и оплата" } },
  { id: 4, block: "buyers", slug: "povernennya", title: { uk: "Обмін і повернення", ru: "Обмен и возврат" } },
  { id: 5, block: "buyers", slug: "oplata-chastynamy", title: { uk: "Оплата частинами", ru: "Оплата частями" } },
];

export const contacts = {
  phones: ["+380950915222", "+380991717925", "+380507388811"],
  email: "uzh.tehnika77@gmail.com",
  address: { uk: "м. Ужгород", ru: "г. Ужгород" } satisfies TR,
};

// ─────────────────────────────────────────────────────────────────────────────
// Нова Пошта (моки довідників)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * ⚠️ Мок довідника НП існує ТІЛЬКИ для офлайн-режиму (NEXT_PUBLIC_USE_MOCKS=true).
 * Бойовий калькулятор ходить у РЕАЛЬНІ /api/v1/delivery/* (див. http.ts) — саме тому,
 * що ці сім міст колись і були «обмеженою кількістю НП», яку бачив користувач.
 * Форма даних тут ОБОВ'ЯЗКОВО повторює схему бекенда, інакше мок знову почне
 * розходитись з реальністю й ховати баги.
 */
export const npCities: NPCityOut[] = [
  { ref: "np-uzh", delivery_city_ref: "city-uzh", name: "Ужгород", present: "м. Ужгород, Закарпатська обл.", area: "Закарпатська обл.", warehouses_count: 3 },
  { ref: "np-kyiv", delivery_city_ref: "city-kyiv", name: "Київ", present: "м. Київ, Київська обл.", area: "Київська обл.", warehouses_count: 3 },
  { ref: "np-lviv", delivery_city_ref: "city-lviv", name: "Львів", present: "м. Львів, Львівська обл.", area: "Львівська обл.", warehouses_count: 2 },
  { ref: "np-mukachevo", delivery_city_ref: "city-muk", name: "Мукачево", present: "м. Мукачево, Закарпатська обл.", area: "Закарпатська обл.", warehouses_count: 1 },
  { ref: "np-odesa", delivery_city_ref: "city-od", name: "Одеса", present: "м. Одеса, Одеська обл.", area: "Одеська обл.", warehouses_count: 1 },
  { ref: "np-kharkiv", delivery_city_ref: "city-kh", name: "Харків", present: "м. Харків, Харківська обл.", area: "Харківська обл.", warehouses_count: 1 },
  { ref: "np-dnipro", delivery_city_ref: "city-dn", name: "Дніпро", present: "м. Дніпро, Дніпропетровська обл.", area: "Дніпропетровська обл.", warehouses_count: 1 },
];

const wh = (
  ref: string,
  number: string,
  description: string,
  service_type: string,
  place_max_weight_kg: number,
): NPWarehouseOut => ({
  ref,
  number,
  description,
  short_address: description,
  category: service_type === "WarehousePostomat" ? "Поштомат" : "Відділення",
  service_type,
  max_width_cm: null,
  max_height_cm: null,
  max_length_cm: null,
  place_max_weight_kg,
});

export const npWarehouses: Record<string, NPWarehouseOut[]> = {
  "np-uzh": [
    wh("wh-uzh-1", "1", "Відділення №1: вул. Гагаріна, 101", "WarehouseWarehouse", 1000),
    wh("wh-uzh-4", "4", "Відділення №4: вул. Минайська, 8/2", "WarehouseWarehouse", 1000),
    wh("wh-uzh-p2", "20302", "Поштомат №20302: вул. Собранецька, 145", "WarehousePostomat", 20),
  ],
  "np-kyiv": [
    wh("wh-kyiv-1", "1", "Відділення №1: вул. Пирогівський шлях, 135", "WarehouseWarehouse", 1100),
    wh("wh-kyiv-55", "55", "Відділення №55: вул. Хрещатик, 22", "WarehouseWarehouse", 30),
    wh("wh-kyiv-p9", "40901", "Поштомат №40901: ТРЦ Ocean Plaza", "WarehousePostomat", 20),
  ],
  "np-lviv": [
    wh("wh-lviv-2", "2", "Відділення №2: вул. Городоцька, 359", "WarehouseWarehouse", 1000),
    wh("wh-lviv-17", "17", "Відділення №17: просп. Чорновола, 67", "WarehouseWarehouse", 500),
  ],
  "np-mukachevo": [wh("wh-muk-1", "1", "Відділення №1: вул. Духновича, 25", "WarehouseWarehouse", 1000)],
  "np-odesa": [wh("wh-od-1", "1", "Відділення №1: вул. Балківська, 63", "WarehouseWarehouse", 1000)],
  "np-kharkiv": [wh("wh-kh-1", "1", "Відділення №1: вул. Полтавський Шлях, 115", "WarehouseWarehouse", 1000)],
  "np-dnipro": [wh("wh-dn-1", "1", "Відділення №1: вул. Набережна Перемоги, 32", "WarehouseWarehouse", 1000)],
};

export type { BannerOut, MenuItemOut, NewsPostOut, StaticPageOut };

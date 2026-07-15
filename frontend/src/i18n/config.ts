/**
 * Тонкий шар i18n. Без next-intl: локаль живе виключно в сегменті шляху,
 * жодних куків/заголовків у ключі кешу (ARCHITECTURE §2.2 — «мова завжди в шляху,
 * не в заголовку», Vary не використовуємо).
 */

export const locales = ["uk", "ru"] as const;
export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "uk";

/** Аліаси, які редіректимо на канонічну локаль (старі URL, звички). */
export const localeAliases: Record<string, Locale> = {
  ua: "uk",
  "uk-ua": "uk",
  "ru-ua": "ru",
};

/** Підпис для перемикача мови. */
export const localeLabels: Record<Locale, string> = {
  uk: "UA",
  ru: "RU",
};

/** hreflang для <link rel="alternate">. */
export const localeHreflang: Record<Locale, string> = {
  uk: "uk-UA",
  ru: "ru-UA",
};

/** Значення lang, яке чекає бекенд Django Ninja (?lang=uk|ru). */
export const localeToApiLang: Record<Locale, "uk" | "ru"> = {
  uk: "uk",
  ru: "ru",
};

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

/**
 * Префіксує шлях локаллю: ("uk", "/cart") → "/uk/cart".
 *
 * ⚠️ Якщо локаль у шляху ВЖЕ Є — вона ЗАМІНЮЄТЬСЯ, а не дублюється.
 *
 * Це не теорія. `link_url` банерів у CMS заведені з локаллю всередині («/uk/c/5609790»),
 * і наївний префікс давав `/uk/uk/c/5609790` → 404 на головній сторінці, а на RU-версії
 * ще й мікс двох мов — `/ru/uk/c/…`. Джерело шляху (БД, CMS, редактор) ми не контролюємо,
 * тому нормалізація мусить жити ТУТ, в одній функції, а не в кожному місці виклику.
 */
export function localePath(locale: Locale, path = "/"): string {
  if (!path || path === "/") return `/${locale}`;

  // Зовнішні посилання й якорі локаллю не префіксуються взагалі.
  if (/^([a-z]+:)?\/\//i.test(path) || path.startsWith("#") || path.startsWith("mailto:")) {
    return path;
  }

  const withSlash = path.startsWith("/") ? path : `/${path}`;
  const segments = withSlash.split("/"); // ["", "uk", "c", "5609790"]

  // Уже є локаль (канонічна або аліас: /ua/…, /uk-ua/…) — міняємо її на потрібну.
  const first = segments[1]?.toLowerCase() ?? "";
  if (isLocale(first) || first in localeAliases) {
    segments[1] = locale;
    return segments.join("/");
  }

  return `/${locale}${withSlash}`;
}

/** Міняє локаль у вже готовому шляху: ("/uk/cart", "ru") → "/ru/cart". */
export function swapLocaleInPath(pathname: string, next: Locale): string {
  const segments = pathname.split("/");
  // segments[0] === "" бо шлях починається зі слеша
  if (segments.length > 1 && isLocale(segments[1])) {
    segments[1] = next;
    return segments.join("/");
  }
  return localePath(next, pathname);
}

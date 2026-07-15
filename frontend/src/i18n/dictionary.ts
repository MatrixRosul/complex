import uk from "./dictionaries/uk.json";
import ru from "./dictionaries/ru.json";
import type { Locale } from "./config";

/** Словник uk — джерело істини для типів. ru мусить мати ту саму форму. */
export type Dictionary = typeof uk;

const dictionaries: Record<Locale, Dictionary> = {
  uk,
  // Приведення типу навмисне: TS звужує JSON-літерали до конкретних рядків,
  // тож ru.json структурно ідентичний, але не тотожний за типом.
  ru: ru as unknown as Dictionary,
};

/** Рекурсивний тип «шлях крізь об'єкт»: "cart.title" | "catalog.sortBy.price_asc" | … */
type Leaves<T> = T extends string
  ? never
  : {
      [K in keyof T & string]: T[K] extends string ? K : `${K}.${Leaves<T[K]>}`;
    }[keyof T & string];

export type TranslationKey = Leaves<Dictionary>;

export type TranslateVars = Record<string, string | number>;

/** Тип функції перекладу — однаковий на сервері й на клієнті. */
export type TFunction = (key: TranslationKey, vars?: TranslateVars) => string;

export function getDictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}

/**
 * Дістає рядок за dot-path і підставляє плейсхолдери {name}.
 *
 * ⚠️ Свідомо повертає сам ключ, якщо переклад відсутній: у проді краще побачити
 * "cart.title" на сторінці, ніж порожнє місце або впалий рендер.
 */
export function translate(
  dict: Dictionary,
  key: TranslationKey,
  vars?: TranslateVars,
): string {
  const value = key
    .split(".")
    .reduce<unknown>(
      (acc, part) =>
        acc && typeof acc === "object" ? (acc as Record<string, unknown>)[part] : undefined,
      dict,
    );

  if (typeof value !== "string") return key;
  if (!vars) return value;

  return value.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

/** Фабрика t() для СЕРВЕРНИХ компонентів. */
export function getT(locale: Locale): TFunction {
  const dict = getDictionary(locale);
  return (key, vars) => translate(dict, key, vars);
}

"use client";

import { createContext, useContext, useMemo } from "react";
import type { Locale } from "./config";
import { translate, type Dictionary, type TFunction } from "./dictionary";

type I18nContextValue = {
  locale: Locale;
  dict: Dictionary;
  t: TFunction;
};

const I18nContext = createContext<I18nContextValue | null>(null);

/**
 * Прокидає словник у клієнтські компоненти.
 *
 * Словник серіалізується в RSC-payload один раз на сторінку (~8 КБ) — це дешевше,
 * ніж тягнути окремий i18n-рантайм у бандл клієнта.
 */
export function I18nProvider({
  locale,
  dict,
  children,
}: {
  locale: Locale;
  dict: Dictionary;
  children: React.ReactNode;
}) {
  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      dict,
      t: (key, vars) => translate(dict, key, vars),
    }),
    [locale, dict],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error("useI18n має викликатись усередині <I18nProvider>");
  }
  return ctx;
}

/** Скорочення для найчастішого випадку. */
export function useT(): TFunction {
  return useI18n().t;
}

/** Поточна локаль на клієнті (для побудови посилань). */
export function useLocale(): Locale {
  return useI18n().locale;
}

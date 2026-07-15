"use client";

import { Suspense } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { cn } from "@/lib/utils";
import { useLocale } from "@/i18n/provider";
import { locales, localeLabels, swapLocaleInPath } from "@/i18n/config";

/**
 * Сегментований контрол UA | RU (DESIGN_SYSTEM §4.10).
 *
 * ⚠️ Це ПОСИЛАННЯ, а не кнопки: мова живе в шляху (/uk/… ↔ /ru/…), тож перемикач
 * мусить бути навігацією — інакше краулер не побачить російську версію,
 * а користувач не зможе поділитись посиланням саме на неї.
 *
 * ⚠️ Обгортка в Suspense обов'язкова: useSearchParams вимикає статичну генерацію
 * для всього піддерева. Хедер стоїть на КОЖНІЙ сторінці, тож без цієї межі
 * увесь сайт став би динамічним через один перемикач мови.
 */
export function LocaleSwitcher({ className }: { className?: string }) {
  return (
    <Suspense fallback={<LocaleSwitcherFallback className={className} />}>
      <LocaleSwitcherInner className={className} />
    </Suspense>
  );
}

function LocaleSwitcherInner({ className }: { className?: string }) {
  const current = useLocale();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Фільтри каталогу (?brand=bosch) зберігаємо при зміні мови:
  // токени фасетів мовонезалежні, тож вибірка не «злітає».
  const query = searchParams.toString();
  const suffix = query ? `?${query}` : "";

  return (
    <Frame className={className}>
      {locales.map((locale) => {
        const isActive = locale === current;

        return (
          <Link
            key={locale}
            href={`${swapLocaleInPath(pathname, locale)}${suffix}`}
            hrefLang={locale}
            aria-current={isActive ? "true" : undefined}
            className={itemClass(isActive)}
          >
            {localeLabels[locale]}
          </Link>
        );
      })}
    </Frame>
  );
}

/** Fallback тримає ті самі розміри — жодного зсуву layout при гідратації. */
function LocaleSwitcherFallback({ className }: { className?: string }) {
  const current = useLocale();

  return (
    <Frame className={className}>
      {locales.map((locale) => (
        <span key={locale} className={itemClass(locale === current)}>
          {localeLabels[locale]}
        </span>
      ))}
    </Frame>
  );
}

function Frame({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div
      className={cn("inline-flex items-center rounded-md border border-border p-0.5", className)}
    >
      {children}
    </div>
  );
}

function itemClass(isActive: boolean) {
  return cn(
    "rounded-sm px-2.5 py-1 text-xs font-semibold transition-colors",
    isActive
      ? "bg-brand-subtle text-brand-subtle-foreground"
      : "text-muted-foreground hover:text-foreground",
  );
}

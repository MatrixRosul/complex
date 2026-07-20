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
export function LocaleSwitcher({ className, onBrand }: Props) {
  return (
    <Suspense fallback={<LocaleSwitcherFallback className={className} onBrand={onBrand} />}>
      <LocaleSwitcherInner className={className} onBrand={onBrand} />
    </Suspense>
  );
}

/**
 * `onBrand` — перемикач стоїть на брендовій темно-синій смузі (топбар), а не на фоні
 * сторінки. Штатні токени там непридатні: `text-muted-foreground` — сірий по темно-синьому
 * (нечитабельно), а активний `bg-brand-subtle` — це майже той самий синій, тобто активна
 * мова зливалася б із смугою. На синьому працює тільки шкала білого.
 */
type Props = { className?: string; onBrand?: boolean };

function LocaleSwitcherInner({ className, onBrand }: Props) {
  const current = useLocale();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Фільтри каталогу (?brand=bosch) зберігаємо при зміні мови:
  // токени фасетів мовонезалежні, тож вибірка не «злітає».
  const query = searchParams.toString();
  const suffix = query ? `?${query}` : "";

  return (
    <Frame className={className} onBrand={onBrand}>
      {locales.map((locale) => {
        const isActive = locale === current;

        return (
          <Link
            key={locale}
            href={`${swapLocaleInPath(pathname, locale)}${suffix}`}
            hrefLang={locale}
            aria-current={isActive ? "true" : undefined}
            className={itemClass(isActive, onBrand)}
          >
            {localeLabels[locale]}
          </Link>
        );
      })}
    </Frame>
  );
}

/** Fallback тримає ті самі розміри — жодного зсуву layout при гідратації. */
function LocaleSwitcherFallback({ className, onBrand }: Props) {
  const current = useLocale();

  return (
    <Frame className={className} onBrand={onBrand}>
      {locales.map((locale) => (
        <span key={locale} className={itemClass(locale === current, onBrand)}>
          {localeLabels[locale]}
        </span>
      ))}
    </Frame>
  );
}

function Frame({
  className,
  onBrand,
  children,
}: Props & { children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border p-0.5",
        onBrand ? "border-white/30" : "border-border",
        className,
      )}
    >
      {children}
    </div>
  );
}

function itemClass(isActive: boolean, onBrand?: boolean) {
  if (onBrand) {
    return cn(
      "rounded-sm px-2.5 py-1 text-xs font-semibold transition-colors",
      isActive ? "bg-white text-brand-deep" : "text-white/75 hover:text-white",
    );
  }

  return cn(
    "rounded-sm px-2.5 py-1 text-xs font-semibold transition-colors",
    isActive
      ? "bg-brand-subtle text-brand-subtle-foreground"
      : "text-muted-foreground hover:text-foreground",
  );
}

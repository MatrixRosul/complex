"use client";

import Link from "next/link";
import { Heart, ShoppingCart, Scale } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { useCartStore } from "@/store/cart";
import { useCompareStore } from "@/store/compare";
import { useWishlistStore } from "@/store/wishlist";
import { useHydrated } from "@/hooks/use-hydrated";

/**
 * Іконки з лічильниками: бажання · порівняння · кошик.
 *
 * ⚠️ Лічильники рендеряться ТІЛЬКИ після гідратації: persist-стори читають
 * localStorage у браузері, тож на сервері їх завжди 0. Показати 0, а потім
 * миттєво 3 — це hydration mismatch і видиме «мигання» у користувача.
 *
 * ⚠️ Особистого кабінету немає — іконки профілю тут не буде.
 */
export function HeaderActions() {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const wishCount = useWishlistStore((s) => s.ids.length);
  const compareCount = useCompareStore((s) => s.ids.length);
  const cartCount = useCartStore((s) => s.lines.length);

  return (
    <div className="flex items-center gap-1">
      <IconLink
        href={localePath(locale, "/wishlist")}
        label={t("nav.wishlist")}
        count={hydrated ? wishCount : 0}
        icon={<Heart className="size-6" />}
      />
      {/* ⚠️ БУЛО `hidden sm:inline-flex` — на телефоні (375–428px) іконки порівняння в шапці
          не існувало ЗОВСІМ, хоча ♡ і 🛒 поруч видимі завжди. Людина, що додала товари з
          картки, не бачила ні лічильника, ні входу в /compare: єдиний шлях — бургер-меню
          в два тапи. Порівняння — сценарій, заради якого в магазин техніки й заходять,
          ховати його точку входу саме на мобільному не можна. */}
      <IconLink
        href={localePath(locale, "/compare")}
        label={t("nav.compare")}
        count={hydrated ? compareCount : 0}
        icon={<Scale className="size-6" />}
      />
      <IconLink
        href={localePath(locale, "/cart")}
        label={t("nav.cart")}
        count={hydrated ? cartCount : 0}
        icon={<ShoppingCart className="size-6" />}
      />
    </div>
  );
}

function IconLink({
  href,
  label,
  count,
  icon,
  className,
}: {
  href: string;
  label: string;
  count: number;
  icon: React.ReactNode;
  className?: string;
}) {
  return (
    <Button
      variant="ghost"
      size="icon-lg"
      // size-11 замість дефолтних size-9: у рядку h-20 дев'ятка виглядає загубленою,
      // і зона тапу на мобільному теж виграє (44 px — мінімум за WCAG 2.5.8).
      className={cn("size-11", className)}
      /**
       * ⚠️ Це ПОСИЛАННЯ, а не кнопка: рендериться <a href>, а не <button>.
       * За замовчуванням Base UI вважає nativeButton=true і вішає на елемент семантику
       * й атрибути нативної кнопки — у розмітці з'являвся невалідний `<a type="button">`,
       * а консоль на кожен рендер писала попередження про втрачену семантику.
       */
      nativeButton={false}
      render={
        <Link href={href} aria-label={count > 0 ? `${label} (${count})` : label}>
          <span className="relative">
            {icon}
            {count > 0 && (
              <span className="absolute -right-1.5 -top-1.5 flex min-w-4 items-center justify-center rounded-full bg-contrast px-1 text-[10px] font-semibold leading-4 text-contrast-foreground tnum">
                {count}
              </span>
            )}
          </span>
        </Link>
      }
    />
  );
}

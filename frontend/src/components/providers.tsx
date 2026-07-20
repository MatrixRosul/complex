"use client";

import { ThemeProvider } from "next-themes";
import { FoxWidget } from "@/components/assistant/fox-widget";
import { CartDrawer } from "@/components/cart/cart-drawer";
import { CatalogMenuProvider } from "@/components/layout/catalog-menu";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { I18nProvider } from "@/i18n/provider";
import type { Locale } from "@/i18n/config";
import type { Dictionary } from "@/i18n/dictionary";

export function Providers({
  locale,
  dict,
  children,
}: {
  locale: Locale;
  dict: Dictionary;
  children: React.ReactNode;
}) {
  return (
    <I18nProvider locale={locale} dict={dict}>
      <ThemeProvider
        attribute="class"
        defaultTheme="light"
        enableSystem
        // Клас .dark ставиться інлайн-скриптом ДО гідратації → нуль спалаху білим.
        disableTransitionOnChange
      >
        <TooltipProvider>
          {/* Кнопка «Каталог» живе в хедері (layout), а розгорнутий список — на
              головній (page). Спільний стан мусить бути вище за обох. */}
          <CatalogMenuProvider>{children}</CatalogMenuProvider>
          {/* Панель кошика — глобальна: «Купити» тиснуть з картки в сітці, зі сторінки
              товару і з порівняння, а відкриватись вона мусить скрізь однаково.
              Вміст монтується лише коли вона відкрита — див. cart-drawer.tsx. */}
          <CartDrawer />
          <FoxWidget />
          {/* offset — щоб тости стакались НАД лисичкою, а не поверх неї:
              обидва живуть у правому нижньому куті. */}
          <Toaster position="bottom-right" offset={88} />
        </TooltipProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}

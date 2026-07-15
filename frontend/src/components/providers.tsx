"use client";

import { ThemeProvider } from "next-themes";
import { FoxWidget } from "@/components/assistant/fox-widget";
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
          {children}
          <FoxWidget />
          {/* offset — щоб тости стакались НАД лисичкою, а не поверх неї:
              обидва живуть у правому нижньому куті. */}
          <Toaster position="bottom-right" offset={88} />
        </TooltipProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}

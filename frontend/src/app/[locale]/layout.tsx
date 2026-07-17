import type { Metadata, Viewport } from "next";
import { notFound } from "next/navigation";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Providers } from "@/components/providers";
import { getDictionary } from "@/i18n/dictionary";
import { isLocale, localeHreflang, locales, type Locale } from "@/i18n/config";
import { inter } from "../fonts";
import "../globals.css";

/**
 * ⚠️ Це КОРЕНЕВИЙ layout (тут <html> і <body>), а не вкладений.
 *
 * Причина: атрибут lang мусить бути в SSR-розмітці, а не проставлятись скриптом
 * після гідратації. lang="uk" на сторінці, яка насправді російською, — це і
 * неправильний переніс слів, і неправильна вимова у скрінрідера, і сигнал
 * пошуковику про мову. Сегмент [locale] відомий тільки тут, тому <html> живе тут.
 * app/layout.tsx свідомо відсутній.
 */
export function generateStaticParams() {
  return locales.map((locale) => ({ locale }));
}

/**
 * ⚠️ Раніше цього export взагалі не було — Next віддавав дефолт
 * `width=device-width, initial-scale=1`, тобто БЕЗ `interactive-widget`.
 *
 * Дефолт браузера — `interactive-widget=resizes-visual`: коли виїжджає екранна
 * клавіатура, лейаут-вьюпорт НЕ вкорочується. Для звичайної сторінки це непомітно,
 * але для `fixed`-панелі чату означало, що її низ (поле вводу) ховається під
 * клавіатурою. `resizes-content` вкорочує саме лейаут-вьюпорт — і fixed-панель
 * сама стає рівно на видиму частину екрана.
 *
 * Працює на Android/Chrome. iOS Safari цей meta ігнорує — там те саме робить
 * VisualViewport API (hooks/use-visual-viewport.ts). Потрібні обидва.
 *
 * initial-scale=1 без maximum-scale — зум пальцями лишається доступним (WCAG 1.4.4).
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  interactiveWidget: "resizes-content",
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const safe: Locale = isLocale(locale) ? locale : "uk";
  const dict = getDictionary(safe);

  return {
    // ⚠️ Медіа й канонікали приходять ВІДНОСНИМИ шляхами (`/media/…`, `/uk/p/…`).
    // Без metadataBase Next абсолютизує їх від http://localhost:3000 (свій дефолт) —
    // тобто og:image і canonical вели б на порт, якого в цьому проєкті взагалі немає.
    metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3001"),
    title: {
      default: `${dict.brand.name} — ${dict.brand.tagline}`,
      template: `%s · ${dict.brand.name}`,
    },
    description: dict.brand.tagline,
    // hreflang: обидві мови — рівноправні сторінки, не дублі.
    alternates: {
      languages: Object.fromEntries(
        locales.map((l) => [localeHreflang[l], `/${l}`]),
      ),
    },
  };
}

export default async function RootLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  // Невідома локаль (/de/…) — 404, а не мовчазний фолбек на uk:
  // фолбек породив би дублі контенту під чужими URL.
  if (!isLocale(locale)) notFound();

  const dict = getDictionary(locale);

  return (
    // suppressHydrationWarning — для next-themes: його інлайн-скрипт дописує
    // class="dark" ДО гідратації (щоб не було спалаху білим).
    <html lang={locale} suppressHydrationWarning className={`${inter.variable} h-full`}>
      <body className="flex min-h-full flex-col antialiased">
        <Providers locale={locale} dict={dict}>
          <Header locale={locale} />
          <main className="flex-1">{children}</main>
          <Footer locale={locale} />
        </Providers>
      </body>
    </html>
  );
}

import { BannerCard } from "@/components/cms/banner-card";
import type { Locale } from "@/i18n/config";
import type { BannerOut } from "@/lib/api/types";

/**
 * Два слоти реклами каталогу — щоб inline-сайдбар (головна) і dropdown (будь-яка сторінка)
 * малювали РІВНО ту саму рекламу, а не кожен свій варіант.
 *
 * • <PromoBanner> — ШИРОКИЙ горизонтальний банер праворуч від списку у стані СПОКОЮ
 *   (каталог закритий). Референс denika: список зліва + промо справа при завантаженні.
 * • <SideAd> — ВУЗЬКА вертикальна смуга (~300px) праворуч від підгруп у ВІДКРИТОМУ стані.
 *
 * ⚠️ Текст банера ВШИТИЙ у картинку (поле image перекладне), title/subtitle порожні —
 * тому scrim вимкнено (за замовчуванням) і накладний текст не рендериться. h2/p лишені
 * порожніми на випадок, якщо колись банер піде з реальним підписом (тоді + scrim).
 */
export function PromoBanner({ banner, locale }: { banner: BannerOut; locale: Locale }) {
  return (
    <BannerCard
      banner={banner}
      locale={locale}
      priority
      sizes="(max-width: 1024px) 100vw, 66vw"
      className="group h-full min-h-[300px] w-full flex-1 justify-end p-6"
    >
      <h2 className="text-h2 banner-text">{banner.title}</h2>
      <p className="max-w-md text-sm banner-text-muted">{banner.subtitle}</p>
    </BannerCard>
  );
}

export function SideAd({ banner, locale }: { banner: BannerOut; locale: Locale }) {
  return (
    <BannerCard
      banner={banner}
      locale={locale}
      priority
      sizes="300px"
      className="group h-full min-h-[300px] justify-end p-5"
    >
      <h2 className="text-h3 banner-text">{banner.title}</h2>
      <p className="text-sm banner-text-muted">{banner.subtitle}</p>
    </BannerCard>
  );
}

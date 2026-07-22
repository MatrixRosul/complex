import { BannerCard } from "@/components/cms/banner-card";
import { cn } from "@/lib/utils";
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
 *
 * ⚠️ `rounded-none border-0` — не косметика: обидва слоти стоять УСЕРЕДИНІ суцільної
 * панелі каталогу, яка сама тримає рамку й заокруглення по зовнішніх краях. Власна
 * рамка банера домальовувала б другу лінію поруч із роздільником колонки.
 */
export function PromoBanner({ banner, locale }: { banner: BannerOut; locale: Locale }) {
  return (
    <BannerCard
      banner={banner}
      locale={locale}
      priority
      sizes="(max-width: 1024px) 100vw, 22vw"
      className="group h-full min-h-[300px] w-full justify-end rounded-none border-0 p-4"
    >
      <h2 className="text-h3 banner-text">{banner.title}</h2>
      <p className="text-sm banner-text-muted">{banner.subtitle}</p>
    </BannerCard>
  );
}

/**
 * РЯД ПРОМО-БАНЕРІВ праворуч від каталогу — до трьох в один ряд (референс denika.ua:
 * там це три вертикальні картки поруч, а не один широкий банер).
 *
 * ⚠️ Один банер займає весь ряд, два — половини, три — третини. Тобто замовнику не
 * треба нічого налаштовувати: скільки завів, так і ляже. Розділені тонкою лінією, бо
 * весь блок стоїть усередині суцільної панелі каталогу з єдиною зовнішньою рамкою.
 */
export function PromoRow({ banners, locale }: { banners: BannerOut[]; locale: Locale }) {
  if (banners.length === 0) return null;

  return (
    <div className="flex h-full w-full">
      {banners.map((banner, i) => (
        <div
          key={banner.id}
          className={cn("min-w-0 flex-1", i > 0 && "border-l border-border")}
        >
          <PromoBanner banner={banner} locale={locale} />
        </div>
      ))}
    </div>
  );
}

/**
 * ПРОМО НА ТЕЛЕФОНІ. Окремий компонент, бо обидва слоти вище живуть усередині
 * <CatalogSidebar>, а він `hidden lg:flex` — тобто на мобільному банерів не було
 * ВЗАГАЛІ (замовник це помітив: «на телефоні не показується банери»).
 *
 * ⚠️ Бере `mobile_image_url`, якщо він заведений: широкий банер 1200×800, втиснутий
 * у 375px, перетворюється на смужку. Немає мобільної версії — показуємо десктопну,
 * обрізану за `focal_point` (тим самим, що видно в адмінці).
 */
export function MobilePromo({ banner, locale }: { banner: BannerOut; locale: Locale }) {
  const forMobile = banner.mobile_image_url
    ? { ...banner, image_url: banner.mobile_image_url }
    : banner;

  return (
    <BannerCard
      banner={forMobile}
      locale={locale}
      sizes="100vw"
      className="group aspect-[16/9] w-full justify-end p-4"
    >
      <h2 className="text-h3 banner-text">{banner.title}</h2>
      <p className="text-sm banner-text-muted">{banner.subtitle}</p>
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
      className="group h-full min-h-[300px] justify-end rounded-none border-0 p-5"
    >
      <h2 className="text-h3 banner-text">{banner.title}</h2>
      <p className="text-sm banner-text-muted">{banner.subtitle}</p>
    </BannerCard>
  );
}

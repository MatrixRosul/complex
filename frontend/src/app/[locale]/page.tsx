import Link from "next/link";

import { BannerCarousel } from "@/components/cms/banner-carousel";
import { MobilePromo, PromoRow, SideAd } from "@/components/cms/banner-slots";
import { BrandStrip } from "@/components/home/brand-strip";
import { CatalogSidebar } from "@/components/home/catalog-sidebar";
import { RecentlyViewed } from "@/components/home/recently-viewed";
import { TrustStrip } from "@/components/home/trust-strip";
import { RelatedProducts } from "@/components/product/related-products";
import { api } from "@/lib/api";
import { emblemMap } from "@/lib/category-icons";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import type { CollectionKey } from "@/lib/api/types";
import type { TranslationKey } from "@/i18n/dictionary";

/** ISR: ADR-023 — time-based fallback поверх on-demand revalidateTag після синку. */
export const revalidate = 600;

/**
 * Добірки ЗА ПРАПОРЦЕМ товару. Заголовок мусить точно відповідати полю, за яким добірка
 * зібрана: «Хіти» = is_featured, «Акції» = є справжня стара ціна, «Новинки» = created_at,
 * «Уцінка» = condition ≠ «Новий». Придумувати тут маркетинг — означає обіцяти те, чого
 * в базі немає (саме за це знесено старі демо-банери).
 *
 * ⚠️ ЗАРАЗ ТРИ З ЧОТИРЬОХ ПОРОЖНІ: у БД 0 товарів з is_featured, 0 зі старою ціною і 0
 * з уцінкою — тобто малюються лише «Новинки». Тому головну несуть НЕ ці добірки, а секції
 * по категоріях (`by_category`) нижче: вони тримаються на самому каталозі й наповнені
 * завжди. Щойно замовник поставить галочку в адмінці — блок з'явиться сам.
 *
 * «Акції» перед «Хітами» навмисно: знижка — найсильніший привід клікнути.
 */
const FLAG_SECTIONS: { key: CollectionKey; title: TranslationKey; href: string }[] = [
  { key: "sale", title: "home.sale", href: "/catalog" },
  { key: "featured", title: "home.bestsellers", href: "/catalog" },
  { key: "new", title: "home.newArrivals", href: "/catalog" },
  { key: "clearance", title: "home.clearance", href: "/catalog" },
];

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = getT(locale);
  const lang = localeToApiLang[locale];

  const [banners, categories, collections, brands] = await Promise.all([
    api.getBanners(lang),
    api.getCategoryTree(lang),
    api.getCollections(lang),
    api.getBrands(lang, 12),
  ]);

  /**
   * ДВА СЛОТИ РЕКЛАМИ КАТАЛОГУ (референс denika.ua):
   *   • promo   — ШИРОКИЙ банер праворуч від списку у стані СПОКОЮ (каталог закритий).
   *               home_promo → слайдер → перший-ліпший (крім того, що вже пішов у бічний).
   *   • sideAd  — ВУЗЬКА вертикальна реклама праворуч від підгруп у ВІДКРИТОМУ стані (home_side).
   * Банерів у БД нема → обидва null, права зона просто порожня (жодної заглушки — за
   * фейкові промо в цьому проєкті вже зносили демо-банери).
   *
   * ⚠️ ОКРЕМОЇ HERO-СЕКЦІЇ НИЖЧЕ БІЛЬШЕ НЕМАЄ. У denika-моделі широкий банер стоїть
   * ПОРУЧ ІЗ КАТАЛОГОМ угорі — це і є hero. Другий великий банер під ним показував би
   * промо того самого класу вдруге (дублювання, на яке поскаржився замовник) і зіштовхував
   * би реальний контент — каруселі категорій — донизу. Тому промо тепер живе рівно в одному
   * місці: правій зоні каталогу. Жодного банера не втрачено як РОЛЬ (промо + бічна реклама).
   *
   * ⚡ КІЛЬКА ПРОМО = СЛАЙДЕР. Раніше в слоті показувався лише ПЕРШИЙ банер, а решта мовчки
   * чекали черги — замовник заводив кілька й бачив одне. Тепер усі промо йдуть у <BannerCarousel>
   * (автопрокрутка + стрілки), а один банер він рендерить як звичайний, без органів керування.
   */
  const sideBanner = banners.find((b) => b.placement === "home_side") ?? null;
  const promoBanners = banners.filter(
    (b) =>
      b.id !== sideBanner?.id &&
      (b.placement === "home_promo" || b.placement === "home_slider"),
  );

  /**
   * ПО ТРИ В РЯД (референс denika.ua: праворуч від каталогу стоять три вертикальні
   * банери поруч, а не один широкий). Групи — це слайди: 1-3 банери дають один слайд
   * без стрілок, 4+ — карусель гортає наступну трійку. Так жоден заведений банер не
   * лишається невидимим і водночас не треба вигадувати «скільки показувати».
   */
  const promoGroups = promoBanners.reduce<(typeof promoBanners)[]>((groups, banner, i) => {
    if (i % 3 === 0) groups.push([]);
    groups[groups.length - 1].push(banner);
    return groups;
  }, []);

  return (
    <div className="container-complex flex flex-col gap-12 py-6">
      {/* ── Каталог зліва + права зона (промо / підгрупи+реклама) ─────────
          Перший екран головної (референс denika.ua). Закрито → список + широкий промо;
          відкрито (ховер/кнопка) → список + підгрупи + вузька реклама. Логіку перемикання
          тримає сам <CatalogSidebar> за станом каталогу. */}
      <CatalogSidebar
        categories={categories}
        iconOf={emblemMap(categories)}
        promoBanner={
          promoGroups.length > 0 ? (
            <BannerCarousel
              slides={promoGroups.map((group, i) => (
                <PromoRow key={i} banners={group} locale={locale} />
              ))}
            />
          ) : null
        }
        sideAd={sideBanner ? <SideAd banner={sideBanner} locale={locale} /> : null}
      />

      {/* ── Промо на ТЕЛЕФОНІ ────────────────────────────────────────────
          Банери десктопу лежать усередині <CatalogSidebar>, а він `hidden lg:flex` —
          тому на мобільному реклами не було взагалі. Тут той самий слайдер, але
          мобільними картинками (див. <MobilePromo>) і лише на вузьких екранах. */}
      {promoBanners.length > 0 && (
        <div className="lg:hidden">
          <BannerCarousel
            slides={promoBanners.map((b) => (
              <MobilePromo key={b.id} banner={b} locale={locale} />
            ))}
          />
        </div>
      )}

      {/* ── Категорії: плитки з фото товару, без лічильників ──────────────
          ⚠️ `lg:hidden` — плитки й новий <CatalogSidebar> показують ОДНЕ Й ТЕ САМЕ
          (кореневі категорії), тому вони не співіснують: на десктопі працює сайдбар,
          на вузьких екранах — плитки (сайдбар там `hidden lg:flex`). Прибирати плитки
          зовсім не можна: на мобільному вони єдиний вхід у каталог прямо зі сторінки,
          решта — тільки бургер, а це на клік більше й невидимо для краулера. */}
      {/* ⏸️ ПЛИТКИ КАТЕГОРІЙ НА МОБІЛЬНОМУ ТИМЧАСОВО ВИМКНЕНІ — на прохання замовника:
          мобільна версія ще не доопрацьована, і цей блок там поки виглядає зайвим.
          ⚠️ Плата за це: на телефоні єдиний вхід у каталог тепер бургер-меню (+1 клік,
          і краулер не бачить посилань на категорії прямо з головної). Тому це саме
          ТИМЧАСОВО: щойно займемось мобільною версткою — повернути, розкоментувавши
          цей блок І дописавши import { CategoryTiles } from "@/components/home/category-tiles".
      <div className="lg:hidden">
        <CategoryTiles categories={categories} locale={locale} title={t("home.categories")} />
      </div>
      */}

      {/* ── Бренди: вхід у каталог для тих, хто прийшов «за Bosch» ────── */}
      <BrandStrip brands={brands} locale={locale} title={t("home.brands")} />

      {/* ── Секції по категоріях ──────────────────────────────────────────
          Головний блок сторінки: наповнений завжди, бо збирається з каталогу, а не
          з галочок замовника. Заголовок веде В САМУ КАТЕГОРІЮ (а не в загальний
          каталог) — інакше «Дивитись усі» під заголовком «Вбудована техніка» вело б
          у список з усіма 314 товарами, і людина губила б контекст. */}
      {collections.by_category.map((section) => (
        <RelatedProducts
          key={section.id}
          title={section.name}
          products={section.products}
          action={
            <Link
              href={localePath(locale, `/catalog/${section.slug}`)}
              className="text-sm text-foreground hover:underline"
            >
              {t("home.toCategory")}
            </Link>
          }
        />
      ))}

      {/* ── Добірки за прапорцем ──────────────────────────────────────────
          ⚠️ ПОРОЖНЯ ДОБІРКА = БЛОКУ НЕМАЄ. Не заголовок над порожнечею, не скелет,
             не «товари скоро з'являться» — блок просто не рендериться. */}
      {FLAG_SECTIONS.map(({ key, title, href }) => {
        const products = collections[key];
        if (products.length === 0) return null;

        return (
          <RelatedProducts
            key={key}
            title={t(title)}
            products={products}
            action={
              <Link
                href={localePath(locale, href)}
                className="text-sm text-foreground hover:underline"
              >
                {t("home.showAll")}
              </Link>
            }
          />
        );
      })}

      {/* ── Ви переглядали ───────────────────────────────────────────────
          Клієнтський блок: id лежать у localStorage, ціни тягне /products/bulk.
          Порожня історія → компонент повертає null, і блоку немає взагалі. */}
      <RecentlyViewed />

      {/* ── Смуга довіри: замикає сторінку перед футером ──────────────── */}
      <TrustStrip locale={locale} />
    </div>
  );
}

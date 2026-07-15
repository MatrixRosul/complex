import Link from "next/link";
import { Mail, MapPin, Phone, ShieldCheck, Truck } from "lucide-react";

import { api } from "@/lib/api";
import { safe } from "@/lib/api/safe";
import { FALLBACK_CONTACTS } from "@/lib/site";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatPhone } from "@/lib/format";

export async function Footer({ locale }: { locale: Locale }) {
  const t = getT(locale);
  const lang = localeToApiLang[locale];

  // safe(): футер теж у layout — недоступний бекенд не має валити сторінку цілком.
  const [categories, menuItems, contacts] = await Promise.all([
    safe(api.getCategoryTree(lang), []),
    safe(api.getMenuItems(lang), []),
    safe(api.getContacts(lang), FALLBACK_CONTACTS(lang)),
  ]);

  const infoItems = menuItems.filter((m) => m.block === "info");
  const buyersItems = menuItems.filter((m) => m.block === "buyers");

  return (
    <footer className="mt-16 border-t border-border bg-muted/40">
      <div className="container-complex grid grid-cols-2 gap-8 py-10 lg:grid-cols-4">
        {/* ── Каталог ─────────────────────────────────────────────── */}
        <nav aria-label={t("nav.catalog")}>
          <h2 className="text-sm font-semibold text-foreground">{t("nav.catalog")}</h2>
          <ul className="mt-3 space-y-2">
            {categories.map((cat) => (
              <li key={cat.id}>
                <Link
                  href={localePath(locale, `/catalog/${cat.slug}`)}
                  className="text-sm text-muted-foreground hover:text-primary"
                >
                  {cat.name}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        {/* ── Інформація ──────────────────────────────────────────── */}
        <nav aria-label={t("footer.info")}>
          <h2 className="text-sm font-semibold text-foreground">{t("footer.info")}</h2>
          <ul className="mt-3 space-y-2">
            {infoItems.map((item) => (
              <li key={item.id}>
                <Link
                  href={localePath(locale, item.url)}
                  className="text-sm text-muted-foreground hover:text-primary"
                >
                  {item.title}
                </Link>
              </li>
            ))}
            <li>
              <Link
                href={localePath(locale, "/news")}
                className="text-sm text-muted-foreground hover:text-primary"
              >
                {t("news.title")}
              </Link>
            </li>
          </ul>
        </nav>

        {/* ── Покупцям ────────────────────────────────────────────── */}
        <nav aria-label={t("footer.forBuyers")}>
          <h2 className="text-sm font-semibold text-foreground">{t("footer.forBuyers")}</h2>
          <ul className="mt-3 space-y-2">
            {buyersItems.map((item) => (
              <li key={item.id}>
                <Link
                  href={localePath(locale, item.url)}
                  className="text-sm text-muted-foreground hover:text-primary"
                >
                  {item.title}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        {/* ── Контакти + час роботи ───────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-foreground">{t("footer.contacts")}</h2>

          <ul className="mt-3 space-y-2">
            {contacts.phones.map((phone) => (
              <li key={phone}>
                <a
                  href={`tel:${phone}`}
                  className="flex items-center gap-2 text-sm text-muted-foreground tnum hover:text-primary"
                >
                  <Phone aria-hidden className="size-4 shrink-0" />
                  {formatPhone(phone)}
                </a>
              </li>
            ))}
            {/* ⚠️ `min-w-0` + `break-all` на пошті — фікс горизонтального скролу на ВСІХ
                сторінках. Футер стоїть у двоколонковому гріді, і на 390px колонка контактів
                = 163px, а «uzh.tehnika77@gmail.com» не переноситься (немає пробілів) і
                займає 182px. Документ роздувався до scrollWidth 393 при viewport 390 —
                на телефоні вбік їздив увесь сайт, включно з каталогом і кошиком.
                Іконкам потрібен shrink-0, інакше замість тексту стискались вони. */}
            <li className="min-w-0">
              <a
                href={`mailto:${contacts.email}`}
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-primary"
              >
                <Mail aria-hidden className="size-4 shrink-0" />
                <span className="min-w-0 break-all">{contacts.email}</span>
              </a>
            </li>
            <li className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
              <MapPin aria-hidden className="size-4 shrink-0" />
              <span className="min-w-0 break-words">{contacts.address}</span>
            </li>
          </ul>

          <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("footer.workingHours")}
          </h3>
          <dl className="mt-2 space-y-1">
            {contacts.working_hours.map((wh) => (
              <div key={wh.days} className="flex items-center gap-2 text-sm">
                <dt className="text-muted-foreground">{wh.days}</dt>
                <dd className="font-medium text-foreground tnum">{wh.time}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>

      {/* ── Нижня смуга: те, що продає довіру ───────────────────────── */}
      <div className="border-t border-border">
        <div className="container-complex flex flex-col gap-3 py-5 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <span className="flex items-center gap-1.5">
              <ShieldCheck aria-hidden className="size-4" />
              {t("footer.warranty")}
            </span>
            <span className="flex items-center gap-1.5">
              <Truck aria-hidden className="size-4" />
              {t("footer.deliveryBy")}
            </span>
            <span className="flex items-center gap-1.5">
              <MapPin aria-hidden className="size-4" />
              {t("footer.pickup")}
            </span>
          </div>

          <span className="tnum">
            © {new Date().getFullYear()} {t("brand.name")}. {t("footer.rights")}.
          </span>
        </div>
      </div>
    </footer>
  );
}

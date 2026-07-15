import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { getT } from "@/i18n/dictionary";
import { defaultLocale, localePath } from "@/i18n/config";
import { cn } from "@/lib/utils";

/**
 * 404 усередині локалі.
 *
 * ⚠️ not-found.tsx не отримує params (Next рендерить його поза сегментом),
 * тому мова тут — дефолтна. Для 404-сторінки це прийнятний компроміс:
 * альтернатива — тягнути локаль із заголовків, а ми домовились її туди не класти.
 */
export default function NotFound() {
  const t = getT(defaultLocale);

  return (
    <div className="container-complex flex flex-col items-center gap-4 py-24 text-center">
      <p className="text-display text-muted-foreground tnum">404</p>
      <h1 className="text-h1 text-foreground">{t("common.notFound")}</h1>
      <p className="text-sm text-muted-foreground">{t("common.notFoundHint")}</p>

      <Link
        href={localePath(defaultLocale)}
        className={cn(buttonVariants({ size: "xl" }), "mt-4")}
      >
        {t("common.goHome")}
      </Link>
    </div>
  );
}

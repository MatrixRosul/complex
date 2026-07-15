import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { getT } from "@/i18n/dictionary";
import { localePath, type Locale } from "@/i18n/config";

/** Хлібні крихти. Серверний компонент — жодного інтерактиву тут не потрібно. */
export function Breadcrumbs({
  locale,
  items,
}: {
  locale: Locale;
  items: { label: string; href?: string }[];
}) {
  const t = getT(locale);

  return (
    <nav aria-label="breadcrumb">
      <ol className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
        <li className="flex items-center gap-1">
          <Link href={localePath(locale)} className="hover:text-primary">
            {t("nav.home")}
          </Link>
          <ChevronRight aria-hidden className="size-3" />
        </li>

        {items.map((item, i) => {
          const isLast = i === items.length - 1;

          return (
            <li key={`${item.label}-${i}`} className="flex items-center gap-1">
              {isLast || !item.href ? (
                <span aria-current={isLast ? "page" : undefined} className="text-foreground">
                  {item.label}
                </span>
              ) : (
                <>
                  <Link href={item.href} className="hover:text-primary">
                    {item.label}
                  </Link>
                  <ChevronRight aria-hidden className="size-3" />
                </>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

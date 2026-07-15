"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { useCompareStore } from "@/store/compare";
import { useProductDetails } from "@/hooks/use-bulk-products";
import { useHydrated } from "@/hooks/use-hydrated";
import { ApiErrorState } from "@/components/api-error";

import { CompareTable } from "./compare-table";

export function CompareView() {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const ids = useCompareStore((s) => s.ids);
  const clear = useCompareStore((s) => s.clear);
  const remove = useCompareStore((s) => s.remove);

  // ⚠️ Саме деталі, а не bulk: порівнювати без ХАРАКТЕРИСТИК нічого.
  const { products, isLoading, error, missingIds } = useProductDetails(ids);

  /**
   * ⚠️ Товар, якого більше немає в каталозі (is_active=false / видалений), ЗВІЛЬНЯЄ СЛОТ.
   *
   * Без цього мертвий id вічно жив у localStorage і з'їдав одну з чотирьох позицій ліміту:
   * у таблиці його не видно (API його не віддає), кнопки «прибрати» в нього немає — а
   * додати п'ятий товар не дає тост «Можна порівняти не більше 4 товарів». Єдиним виходом
   * було «Очистити» весь список.
   */
  useEffect(() => {
    if (missingIds.length === 0) return;
    missingIds.forEach(remove);
    toast.info(t("compare.removedUnavailable"));
    // t/remove стабільні; реагуємо саме на появу зниклих id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingIds.join(",")]);

  if (!hydrated || isLoading) {
    return ids.length === 0 && hydrated ? null : <Skeleton className="h-96 w-full" />;
  }

  // Помилка API ≠ «порожньо»: інакше людина вирішить, що її список порівняння зник.
  if (error) return <ApiErrorState onRetry={() => window.location.reload()} />;

  if (ids.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-12 text-center">
        <p className="text-h3 text-foreground">{t("compare.empty")}</p>
        <p className="mt-2 text-sm text-muted-foreground">{t("compare.emptyHint")}</p>
        <Link
          href={localePath(locale, "/catalog")}
          className={cn(buttonVariants({ size: "xl" }), "mt-6")}
        >
          {t("cart.goToCatalog")}
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <CompareTable products={products} />

      <Button
        variant="ghost"
        size="sm"
        className="w-fit text-muted-foreground"
        onClick={clear}
      >
        <Trash2 className="size-4" />
        {t("compare.clear")}
      </Button>
    </div>
  );
}

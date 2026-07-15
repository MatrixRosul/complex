"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button, buttonVariants } from "@/components/ui/button";
import { ProductGrid, ProductGridSkeleton } from "@/components/product/product-grid";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { useWishlistStore } from "@/store/wishlist";
import { useBulkProducts } from "@/hooks/use-bulk-products";
import { useHydrated } from "@/hooks/use-hydrated";
import { ApiErrorState } from "@/components/api-error";

/**
 * Список бажань.
 *
 * ⚠️ У localStorage — тільки id. Ціни й наявність тягнемо bulk-запитом при кожному
 * відкритті: список бажань живе МІСЯЦЯМИ, ціна в ньому протухає гарантовано.
 * Показати збережену ціну тут — це прямий шлях до конфлікту на касі.
 */
export function WishlistView() {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const ids = useWishlistStore((s) => s.ids);
  const clear = useWishlistStore((s) => s.clear);
  const remove = useWishlistStore((s) => s.remove);

  // ⚠️ POST /products/bulk — ЄДИНЕ джерело цін і наявності (у localStorage лише id).
  const { products, isLoading, error, missingIds } = useBulkProducts(ids);

  /**
   * Товар зняли з продажу — bulk його просто не повертає (він у `unavailable_items`).
   * Раніше він мовчки зникав із сітки, а id лишався в localStorage назавжди. Тепер
   * прибираємо його зі стора і кажемо про це вголос — інакше людина губиться, чому
   * лічильник у хедері показує 3, а на сторінці два товари.
   */
  useEffect(() => {
    if (missingIds.length === 0) return;
    missingIds.forEach(remove);
    toast.info(t("wishlist.removedUnavailable"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingIds.join(",")]);

  if (!hydrated) return <ProductGridSkeleton count={4} />;

  // Помилка API ≠ «список порожній» — це два різні екрани.
  if (error) return <ApiErrorState onRetry={() => window.location.reload()} />;

  if (ids.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-12 text-center">
        <p className="text-h3 text-foreground">{t("wishlist.empty")}</p>
        <p className="mt-2 text-sm text-muted-foreground">{t("wishlist.emptyHint")}</p>
        <Link
          href={localePath(locale, "/catalog")}
          className={cn(buttonVariants({ size: "xl" }), "mt-6")}
        >
          {t("cart.goToCatalog")}
        </Link>
      </div>
    );
  }

  if (isLoading) return <ProductGridSkeleton count={ids.length} />;

  return (
    <div className="flex flex-col gap-4">
      <ProductGrid products={products} />

      <Button
        variant="ghost"
        size="sm"
        className="w-fit text-muted-foreground"
        onClick={clear}
      >
        <Trash2 className="size-4" />
        {t("wishlist.clear")}
      </Button>
    </div>
  );
}

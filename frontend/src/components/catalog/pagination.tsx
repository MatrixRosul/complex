"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";
import { useCatalogParams } from "./use-catalog-params";

export function Pagination({ page, pages }: { page: number; pages: number }) {
  const t = useT();
  const { setPage } = useCatalogParams();

  if (pages <= 1) return null;

  // Вікно сторінок: 1 … 4 5 [6] 7 8 … 20 — не друкуємо 20 кнопок.
  const window = new Set<number>([1, pages, page, page - 1, page + 1]);
  const items = [...window]
    .filter((p) => p >= 1 && p <= pages)
    .sort((a, b) => a - b);

  return (
    <nav aria-label={t("catalog.page")} className="flex items-center justify-center gap-1">
      <Button
        variant="outline"
        size="icon-lg"
        aria-label={t("catalog.prev")}
        disabled={page <= 1}
        onClick={() => setPage(page - 1)}
      >
        <ChevronLeft className="size-4" />
      </Button>

      {items.map((p, i) => {
        const prev = items[i - 1];
        const gap = prev !== undefined && p - prev > 1;

        return (
          <span key={p} className="flex items-center gap-1">
            {gap && <span className="px-1 text-muted-foreground">…</span>}
            <Button
              variant={p === page ? "default" : "outline"}
              size="icon-lg"
              aria-current={p === page ? "page" : undefined}
              className={cn("tnum", p === page && "pointer-events-none")}
              onClick={() => setPage(p)}
            >
              {p}
            </Button>
          </span>
        );
      })}

      <Button
        variant="outline"
        size="icon-lg"
        aria-label={t("catalog.next")}
        disabled={page >= pages}
        onClick={() => setPage(page + 1)}
      >
        <ChevronRight className="size-4" />
      </Button>
    </nav>
  );
}

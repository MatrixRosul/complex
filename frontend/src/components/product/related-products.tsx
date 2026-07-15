"use client";

import { useRef } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ProductCard } from "./product-card";
import type { ProductListItem } from "@/lib/api/types";

/**
 * Горизонтальна стрічка товарів (супутні / рекомендовані / знижки).
 * На мобільному — нативний свайп зі snap, на ПК — плюс стрілки.
 */
export function RelatedProducts({
  title,
  products,
  action,
}: {
  title: string;
  products: ProductListItem[];
  /** Посилання «Дивитись усі» справа від заголовка. */
  action?: React.ReactNode;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  if (products.length === 0) return null;

  const scrollBy = (delta: number) => {
    scroller.current?.scrollBy({ left: delta, behavior: "smooth" });
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-h2 text-foreground">{title}</h2>

        <div className="flex items-center gap-2">
          {action}
          <div className="hidden gap-1 md:flex">
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="←"
              onClick={() => scrollBy(-320)}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="→"
              onClick={() => scrollBy(320)}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </div>

      <div
        ref={scroller}
        className="no-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto pb-2"
      >
        {products.map((p) => (
          <ProductCard
            key={p.id}
            product={p}
            className="w-[calc(50%-0.375rem)] shrink-0 snap-start sm:w-56 lg:w-64"
          />
        ))}
      </div>
    </section>
  );
}

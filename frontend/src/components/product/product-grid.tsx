import { cn } from "@/lib/utils";
import type { ProductListItem } from "@/lib/api/types";
import { ProductCard, ProductCardSkeleton } from "./product-card";

/**
 * Сітка каталогу (DESIGN_SYSTEM §4.2).
 *
 * Дві колонки на мобільному, а не одна: побутову техніку порівнюють очима,
 * а не читають. Одна колонка = вдвічі більше скролу і менше товару в полі зору.
 *
 * <640: 2 · 640–1023: 3 · 1024–1439: 4 · ≥1440: 5
 */
export function ProductGrid({
  products,
  className,
  /** Скільки перших карток отримають priority-фото (LCP). */
  priorityCount = 4,
}: {
  products: ProductListItem[];
  className?: string;
  priorityCount?: number;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 lg:grid-cols-4 lg:gap-4 min-[1440px]:grid-cols-5",
        className,
      )}
    >
      {products.map((p, i) => (
        <ProductCard key={p.id} product={p} priority={i < priorityCount} />
      ))}
    </div>
  );
}

export function ProductGridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 lg:grid-cols-4 lg:gap-4 min-[1440px]:grid-cols-5">
      {Array.from({ length: count }, (_, i) => (
        <ProductCardSkeleton key={i} />
      ))}
    </div>
  );
}

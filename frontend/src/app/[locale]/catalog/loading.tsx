import { Skeleton } from "@/components/ui/skeleton";
import { ProductGridSkeleton } from "@/components/product/product-grid";

/** Каталог: сайдбар фасетів + сітка — саме той каркас, який прийде з даними. */
export default function CatalogLoading() {
  return (
    <div className="container-complex flex flex-col gap-5 py-6">
      <Skeleton className="h-5 w-72" />
      <Skeleton className="h-9 w-80" />

      <div className="flex gap-8">
        <aside className="hidden w-[280px] shrink-0 flex-col gap-4 lg:flex">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </aside>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <Skeleton className="h-10 w-full" />
          <ProductGridSkeleton count={9} />
        </div>
      </div>
    </div>
  );
}

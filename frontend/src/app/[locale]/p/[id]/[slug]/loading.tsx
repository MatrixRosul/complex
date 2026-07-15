import { Skeleton } from "@/components/ui/skeleton";

/** Картка товару: галерея зліва, купівельна панель справа, характеристики | опис знизу. */
export default function ProductLoading() {
  return (
    <div className="container-complex flex flex-col gap-10 py-6">
      <Skeleton className="h-5 w-80" />
      <Skeleton className="h-9 w-2/3" />

      <div className="grid gap-8 lg:grid-cols-2">
        <Skeleton className="aspect-square w-full" />

        <div className="flex flex-col gap-4">
          <Skeleton className="h-12 w-48" />
          <Skeleton className="h-10 w-40" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-80 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    </div>
  );
}

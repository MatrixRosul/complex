import type { Metadata } from "next";

import { CartView } from "@/components/cart/cart-view";
import { getT } from "@/i18n/dictionary";
import type { Locale } from "@/i18n/config";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: getT(locale)("cart.title"),
    // Кошик нема сенсу індексувати: у кожного він свій і завжди різний.
    robots: { index: false, follow: true },
  };
}

export default async function CartPage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = getT(locale);

  return (
    <div className="container-complex flex flex-col gap-6 py-6">
      <h1 className="text-h1 text-foreground">{t("cart.title")}</h1>
      <CartView />
    </div>
  );
}

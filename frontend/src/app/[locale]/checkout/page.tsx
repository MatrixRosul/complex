import type { Metadata } from "next";

import { CheckoutForm } from "@/components/checkout/checkout-form";
import { getT } from "@/i18n/dictionary";
import type { Locale } from "@/i18n/config";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: getT(locale)("checkout.title"),
    robots: { index: false, follow: false },
  };
}

export default async function CheckoutPage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = getT(locale);

  return (
    <div className="container-complex flex flex-col gap-6 py-6">
      <h1 className="text-h1 text-foreground">{t("checkout.title")}</h1>
      <CheckoutForm />
    </div>
  );
}

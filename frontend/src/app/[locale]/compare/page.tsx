import type { Metadata } from "next";

import { CompareView } from "@/components/compare/compare-view";
import { getT } from "@/i18n/dictionary";
import type { Locale } from "@/i18n/config";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: getT(locale)("compare.title"),
    robots: { index: false, follow: true },
  };
}

export default async function ComparePage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = getT(locale);

  return (
    <div className="container-complex flex flex-col gap-6 py-6">
      <h1 className="text-h1 text-foreground">{t("compare.title")}</h1>
      <CompareView />
    </div>
  );
}

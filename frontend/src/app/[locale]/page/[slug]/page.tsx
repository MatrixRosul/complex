import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { api } from "@/lib/api";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";

type Params = Promise<{ locale: Locale; slug: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { locale, slug } = await params;
  const page = await api.getStaticPage(slug, localeToApiLang[locale]);
  if (!page) return {};

  return {
    title: page.title,
    alternates: { canonical: localePath(locale, `/page/${slug}`) },
  };
}

export default async function StaticPage({ params }: { params: Params }) {
  const { locale, slug } = await params;

  const page = await api.getStaticPage(slug, localeToApiLang[locale]);
  if (!page) notFound();

  return (
    <div className="container-complex flex max-w-3xl flex-col gap-5 py-8">
      <Breadcrumbs locale={locale} items={[{ label: page.title }]} />

      <h1 className="text-h1 text-foreground">{page.title}</h1>

      {/* Rich HTML із адмінки. Санітизація — на бекенді. */}
      <div
        className="prose prose-zinc max-w-[68ch] dark:prose-invert prose-img:rounded-lg"
        dangerouslySetInnerHTML={{ __html: page.body }}
      />
    </div>
  );
}

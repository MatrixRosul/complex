import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";

import { api } from "@/lib/api";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatDate } from "@/lib/format";

type Params = Promise<{ locale: Locale; slug: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { locale, slug } = await params;
  const post = await api.getNewsPost(slug, localeToApiLang[locale]);
  if (!post) return {};

  return {
    title: post.title,
    description: post.excerpt,
    alternates: { canonical: localePath(locale, `/news/${slug}`) },
    openGraph: {
      type: "article",
      title: post.title,
      description: post.excerpt,
      publishedTime: post.published_at,
      images: post.cover_url ? [post.cover_url] : undefined,
    },
  };
}

export default async function NewsPostPage({ params }: { params: Params }) {
  const { locale, slug } = await params;
  const t = getT(locale);

  const post = await api.getNewsPost(slug, localeToApiLang[locale]);
  if (!post) notFound();

  return (
    <article className="container-complex flex max-w-3xl flex-col gap-5 py-8">
      <Link
        href={localePath(locale, "/news")}
        className="flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-primary"
      >
        <ChevronLeft aria-hidden className="size-4" />
        {t("news.backToList")}
      </Link>

      <time dateTime={post.published_at} className="text-xs text-muted-foreground tnum">
        {t("news.publishedAt")}: {formatDate(post.published_at, locale)}
      </time>

      <h1 className="text-h1 text-foreground">{post.title}</h1>

      {post.cover_url && (
        <div className="relative aspect-[16/9] overflow-hidden rounded-lg border border-border bg-muted">
          <Image
            src={post.cover_url}
            alt=""
            fill
            priority
            sizes="(max-width: 768px) 100vw, 768px"
            className="object-cover"
          />
        </div>
      )}

      {/* Rich HTML. Санітизація — на бекенді. */}
      <div
        className="prose prose-zinc max-w-[68ch] dark:prose-invert prose-img:rounded-lg"
        dangerouslySetInnerHTML={{ __html: post.body }}
      />
    </article>
  );
}

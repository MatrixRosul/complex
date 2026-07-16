import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

import { api } from "@/lib/api";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatDate } from "@/lib/format";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return { title: getT(locale)("news.title") };
}

export default async function NewsListPage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = getT(locale);

  const posts = await api.getNews(localeToApiLang[locale]);

  return (
    <div className="container-complex flex flex-col gap-6 py-6">
      <h1 className="text-h1 text-foreground">{t("news.title")}</h1>

      {posts.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("news.empty")}</p>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {posts.map((post) => (
            <article key={post.id} className="flex flex-col">
              <Link
                href={localePath(locale, `/news/${post.slug}`)}
                className="group flex flex-col gap-3"
              >
                <div className="relative aspect-[16/9] overflow-hidden rounded-lg border border-border bg-muted">
                  {post.cover_url && (
                    <Image
                      src={post.cover_url}
                      alt=""
                      fill
                      sizes="(max-width: 640px) 100vw, 33vw"
                      className="object-cover transition-transform duration-200 group-hover:scale-[1.03]"
                    />
                  )}
                </div>

                <time
                  dateTime={post.published_at}
                  className="text-xs text-muted-foreground tnum"
                >
                  {formatDate(post.published_at, locale)}
                </time>

                <h2 className="text-h3 text-foreground group-hover:underline">
                  {post.title}
                </h2>

                <p className="line-clamp-3 text-sm text-muted-foreground">{post.excerpt}</p>
              </Link>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

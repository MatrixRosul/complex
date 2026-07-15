import { NextResponse } from "next/server";

import { api } from "@/lib/api";
import { safe } from "@/lib/api/safe";
import { isLocale, localeToApiLang } from "@/i18n/config";

/**
 * Підказки для рядка пошуку — легкий ендпоінт, який SearchBar смикає з дебаунсом.
 *
 * Навмисно серверний route-handler, а не прямий виклик api з клієнта: конфіг бекенда
 * (NEXT_PUBLIC_API_URL, next-теги, revalidate) і SSRF-нюанси лишаються на сервері, а
 * назовні йде тонкий JSON. Повертаємо лише поля, потрібні для картки-підказки.
 */
export const runtime = "nodejs";

const LIMIT = 6;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const q = (searchParams.get("q") ?? "").trim();
  const localeParam = searchParams.get("locale") ?? "uk";
  const locale = isLocale(localeParam) ? localeParam : "uk";

  // Менше 2 символів — не ходимо на бекенд, підказок не буде.
  if (q.length < 2) {
    return NextResponse.json({ items: [], total: 0 });
  }

  // safe(): підказки — допоміжна фіча, недоступний бекенд не має кидати 500 у хедер.
  const data = await safe(
    api.getCatalog({ q, page_size: LIMIT }, localeToApiLang[locale]),
    null,
  );

  if (!data) {
    return NextResponse.json({ items: [], total: 0 });
  }

  const items = data.items.slice(0, LIMIT).map((p) => ({
    id: p.id,
    name: p.name,
    slug: p.slug,
    price: p.price,
    old_price: p.old_price,
    main_image_url: p.main_image_url,
  }));

  return NextResponse.json({ items, total: data.total });
}

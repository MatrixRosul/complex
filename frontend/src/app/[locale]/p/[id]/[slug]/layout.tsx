import { notFound } from "next/navigation";

import { api } from "@/lib/api";
import { localeToApiLang, type Locale } from "@/i18n/config";

/**
 * ⚠️ ЦЕЙ LAYOUT ІСНУЄ ЗАРАДИ HTTP-СТАТУСУ 404. Він нічого не малює.
 *
 * ПРОБЛЕМА. `loading.tsx` (сусідній файл) створює Suspense-межу. Next починає СТРІМИТИ
 * відповідь одразу — тобто ВІДПРАВЛЯЄ статус 200 ще до того, як page.tsx дочекався даних.
 * Коли page.tsx потім кличе notFound(), статус змінити вже НЕМОЖЛИВО: у мережу пішов 200,
 * а 404-контент домальовується в тілі. Наслідок — soft-404: сторінка неіснуючого або
 * деактивованого товару віддає 200 OK.
 * Це не косметика: у каталозі 682 товари, з них активні лише 321 — тобто СОТНІ URL, які
 * Google продовжував би тримати в індексі як робочі сторінки.
 *
 * РІШЕННЯ. Layout рендериться ВИЩЕ Suspense-межі, до першого флаша. Перевірку існування
 * товару робимо тут — і notFound() встигає виставити справжній 404. Скелет із loading.tsx
 * при цьому лишається живим для решти сторінки.
 *
 * ⚠️ Другий запит getProduct() тут БЕЗКОШТОВНИЙ: fetch у httpApi мемоїзується Next'ом у
 * межах одного рендера, тож page.tsx дістане цю ж відповідь із кешу, а не піде в мережу.
 */
export default async function ProductLayout({
  children,
  params,
}: LayoutProps<"/[locale]/p/[id]/[slug]">) {
  const { locale, id } = await params;

  const product = await api.getProduct(Number(id), localeToApiLang[locale as Locale]);
  if (!product) notFound();

  return children;
}

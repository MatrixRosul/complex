import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { CheckCircle2, ImageOff, Link2 } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatDateTime, formatPhone, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

type Params = Promise<{ locale: Locale; token: string }>;

/**
 * ⚠️ Сторінка замовлення живе за ПУБЛІЧНИМ ТОКЕНОМ, а не за номером.
 *
 * Номер (CMPX-260711-0042) передбачуваний: дата + лічильник. Роут /order/{number}
 * дозволив би перебрати CMPX-260711-0001…0100 і зібрати ПІБ, телефони й адреси
 * усіх покупців за день. Токен — uuid4, 122 біти ентропії, перебір неможливий.
 *
 * ⚠️ noindex + nofollow: сторінка містить персональні дані. Якщо покупець
 * поділиться посиланням у месенджері, воно не має потрапити в індекс.
 */
export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: getT(locale)("order.thanks"),
    robots: { index: false, follow: false },
  };
}

export default async function OrderPage({ params }: { params: Params }) {
  const { locale, token } = await params;
  const t = getT(locale);

  const order = await api.getOrderByToken(token, localeToApiLang[locale]);

  if (!order) {
    return (
      <div className="container-complex flex flex-col items-center gap-4 py-20 text-center">
        <h1 className="text-h1 text-foreground">{t("order.notFound")}</h1>
        <p className="text-sm text-muted-foreground">{t("order.notFoundHint")}</p>
        <Link href={localePath(locale)} className={cn(buttonVariants({ size: "xl" }), "mt-4")}>
          {t("common.goHome")}
        </Link>
      </div>
    );
  }

  return (
    <div className="container-complex flex max-w-3xl flex-col gap-6 py-10">
      <div className="flex flex-col items-center gap-2 text-center">
        <CheckCircle2 aria-hidden className="size-12 text-stock-in" />
        <h1 className="text-h1 text-foreground">{t("order.thanks")}</h1>
        <p className="text-sm text-muted-foreground">{t("order.thanksHint")}</p>
      </div>

      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-h3 text-foreground tnum">
            {t("order.title", { number: order.number })}
          </h2>
          <span className="rounded-full bg-brand-subtle px-3 py-1 text-xs font-medium text-brand-subtle-foreground">
            {t(`order.statuses.${order.status}` as "order.statuses.new")}
          </span>
        </div>

        <p className="mt-1 text-xs text-muted-foreground tnum">
          {t("order.createdAt")}: {formatDateTime(order.created_at, locale)}
        </p>

        {/* Покупець без кабінету має ЯКИЙСЬ спосіб повернутись до замовлення.
            Єдиний — це посилання. Тому просимо його зберегти. */}
        <p className="mt-4 flex items-start gap-2 rounded-md bg-muted p-3 text-xs text-muted-foreground">
          <Link2 aria-hidden className="mt-0.5 size-4 shrink-0" />
          {t("order.saveLink")}
        </p>
      </div>

      {/* ── Позиції ─────────────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="text-h3 text-foreground">{t("order.items")}</h2>

        <ul className="mt-4 flex flex-col gap-4">
          {order.items.map((item) => (
            <li key={item.product_id} className="flex gap-3">
              <div className="relative size-16 shrink-0">
                {item.main_image_url ? (
                  <Image
                    src={item.main_image_url}
                    alt={item.name}
                    fill
                    sizes="64px"
                    className="object-contain"
                  />
                ) : (
                  <div className="flex size-full items-center justify-center rounded bg-muted">
                    <ImageOff aria-hidden className="size-4 text-muted-foreground" />
                  </div>
                )}
              </div>

              <div className="flex flex-1 flex-col">
                <span className="text-sm font-medium text-foreground">{item.name}</span>
                <span className="product-code text-xs text-muted-foreground">{item.sku}</span>
                <span className="text-xs text-muted-foreground tnum">
                  {formatPrice(item.price, locale)} ₴ × {item.qty}
                </span>
              </div>

              <span className="text-sm font-semibold text-foreground tnum">
                {formatPrice(item.line_total, locale)} ₴
              </span>
            </li>
          ))}
        </ul>

        <div className="mt-4 flex items-baseline justify-between border-t border-border pt-4">
          <span className="text-sm text-muted-foreground">{t("cart.total")}</span>
          <span className="price text-price-xl text-price-regular">
            {formatPrice(order.total, locale)} ₴
          </span>
        </div>
      </section>

      {/* ── Отримувач і доставка ───────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-2">
        <section className="rounded-lg border border-border bg-card p-5">
          <h2 className="text-sm font-semibold text-foreground">{t("order.recipient")}</h2>
          <dl className="mt-3 flex flex-col gap-1 text-sm">
            <dd className="text-foreground">
              {order.last_name} {order.first_name}
            </dd>
            <dd className="text-muted-foreground tnum">{formatPhone(order.phone)}</dd>
            {order.email && <dd className="text-muted-foreground">{order.email}</dd>}
          </dl>
        </section>

        <section className="rounded-lg border border-border bg-card p-5">
          <h2 className="text-sm font-semibold text-foreground">{t("order.deliveryInfo")}</h2>
          <dl className="mt-3 flex flex-col gap-1 text-sm">
            <dd className="text-foreground">
              {t(
                `checkout.deliveryMethod.${order.delivery_method}` as "checkout.deliveryMethod.pickup",
              )}
            </dd>
            {order.np_city_name && (
              <dd className="text-muted-foreground">{order.np_city_name}</dd>
            )}
            {order.np_warehouse_name && (
              <dd className="text-muted-foreground">{order.np_warehouse_name}</dd>
            )}
            {order.delivery_address && (
              <dd className="text-muted-foreground">{order.delivery_address}</dd>
            )}
            {order.ttn && (
              <dd className="mt-1 text-foreground tnum">
                {t("order.ttn")}: {order.ttn}
              </dd>
            )}
          </dl>

          <h3 className="mt-4 text-sm font-semibold text-foreground">
            {t("order.paymentStatus")}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {t(`checkout.paymentMethod.${order.payment_method}` as "checkout.paymentMethod.cod")}
            {" · "}
            {t(
              `order.paymentStatuses.${order.payment_status}` as "order.paymentStatuses.pending",
            )}
          </p>
        </section>
      </div>

      <Link
        href={localePath(locale)}
        className={cn(buttonVariants({ variant: "outline", size: "xl" }), "mx-auto")}
      >
        {t("common.goHome")}
      </Link>
    </div>
  );
}

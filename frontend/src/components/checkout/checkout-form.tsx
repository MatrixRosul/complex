"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";

import { ApiErrorState } from "@/components/api-error";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath, localeToApiLang } from "@/i18n/config";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/types";
import { formatPrice, isValidEmail, isValidPhone, normalizePhone } from "@/lib/format";
import { useCartStore } from "@/store/cart";
import { useCartPreview } from "@/hooks/use-cart-preview";
import { useHydrated } from "@/hooks/use-hydrated";
import type { DeliveryMethod, NPCityOut, NPWarehouseOut, PaymentMethod } from "@/lib/api/types";

const DELIVERY_METHODS: DeliveryMethod[] = [
  "np_warehouse",
  "np_postomat",
  "np_courier",
  "pickup",
  "local_courier",
];

const PAYMENT_METHODS: PaymentMethod[] = ["cod", "prepay", "online", "installment"];

const NP_METHODS = new Set<DeliveryMethod>(["np_warehouse", "np_postomat", "np_courier"]);

/**
 * Чекаут БЕЗ реєстрації (особистого кабінету в проєкті немає).
 *
 * ⚠️ idempotency_key генерується ОДИН раз на відкриття форми, а не на кожен сабміт.
 * Подвійний клік по «Підтвердити», ретрай мережі, F5 у момент відправки — усе це
 * має привести до ОДНОГО замовлення й ОДНОГО платежу. Ключ на кожен клік цю
 * гарантію знищив би.
 *
 * ⚠️ expected_total шлемо разом із замовленням. Сервер перерахує суму з БД, і якщо
 * вона не зійшлася (прайс синкнувся, поки людина заповнювала форму) — поверне 409,
 * а не мовчки спише іншу суму.
 */
export function CheckoutForm() {
  const t = useT();
  const locale = useLocale();
  const router = useRouter();
  const hydrated = useHydrated();

  const lines = useCartStore((s) => s.lines);
  const clearCart = useCartStore((s) => s.clear);
  // ⚠️ `error` НЕ можна ігнорувати. Поки його не діставали, впалий preview давав форму
  // з порожнім списком, сумою «0 ₴» і АКТИВНОЮ кнопкою «Підтвердити»: submit() виходив
  // на `if (!data) return` мовчки — жодного тосту, жодного пояснення, нічого.
  const { data, isLoading, error, reload } = useCartPreview();

  // Один ключ на весь час життя форми.
  const idempotencyKey = useMemo(() => crypto.randomUUID(), []);

  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [comment, setComment] = useState("");

  const [deliveryMethod, setDeliveryMethod] = useState<DeliveryMethod>("np_warehouse");
  const [cityQuery, setCityQuery] = useState("");
  const [cities, setCities] = useState<NPCityOut[]>([]);
  const [city, setCity] = useState<NPCityOut | null>(null);
  const [warehouses, setWarehouses] = useState<NPWarehouseOut[]>([]);
  const [warehouseRef, setWarehouseRef] = useState("");
  const [address, setAddress] = useState("");

  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cod");

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const needsNP = NP_METHODS.has(deliveryMethod);
  const needsAddress = deliveryMethod === "np_courier" || deliveryMethod === "local_courier";
  // Без email немає куди слати квитанцію і немає ідентифікатора платника.
  const emailRequired = paymentMethod === "online" || paymentMethod === "installment";

  useEffect(() => {
    if (cityQuery.trim().length < 2 || city) {
      // Скидання/прапорець мусять спрацювати СИНХРОННО, до await: інакше між
      // зміною входу й відповіддю буде кадр зі старими даними.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCities([]);
      return;
    }
    const timer = setTimeout(() => {
      void api.searchCities(cityQuery).then(setCities);
    }, 300);
    return () => clearTimeout(timer);
  }, [cityQuery, city]);

  useEffect(() => {
    if (!city) {
      // Скидання/прапорець мусять спрацювати СИНХРОННО, до await: інакше між
      // зміною входу й відповіддю буде кадр зі старими даними.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWarehouses([]);
      setWarehouseRef("");
      return;
    }
    void api.getWarehouses(city.ref).then((list) => {
      setWarehouses(list);
      setWarehouseRef(list[0]?.ref ?? "");
    });
  }, [city]);

  const validate = (): boolean => {
    const next: Record<string, string> = {};

    if (!lastName.trim()) next.lastName = t("checkout.required");
    if (!firstName.trim()) next.firstName = t("checkout.required");
    if (!phone.trim()) next.phone = t("checkout.required");
    else if (!isValidPhone(phone)) next.phone = t("checkout.invalidPhone");

    if (emailRequired && !email.trim()) next.email = t("checkout.required");
    else if (email.trim() && !isValidEmail(email)) next.email = t("checkout.invalidEmail");

    if (needsNP && !city) next.city = t("checkout.required");
    if (needsAddress && !address.trim()) next.address = t("checkout.required");

    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async () => {
    if (lines.length === 0) {
      toast.error(t("checkout.emptyCart"));
      return;
    }
    // Кошик не порахувався (мережа/бекенд) — кажемо про це вголос, а не виходимо мовчки.
    if (!data) {
      toast.error(t("checkout.cartLoadFailed"), { description: t("checkout.cartLoadFailedHint") });
      void reload();
      return;
    }
    if (!validate()) return;

    setSubmitting(true);
    try {
      const warehouse = warehouses.find((w) => w.ref === warehouseRef);

      const order = await api.createOrder(
        {
          items: lines,
          // Сервер перевірить: розбіжність → 409, а не тиха зміна суми.
          expected_total: data.subtotal,
          idempotency_key: idempotencyKey,

          last_name: lastName.trim(),
          first_name: firstName.trim(),
          phone: normalizePhone(phone),
          email: email.trim() || undefined,
          comment: comment.trim() || undefined,

          delivery_method: deliveryMethod,
          np_city_ref: city?.ref,
          np_city_name: city?.name,
          np_warehouse_ref: warehouse?.ref,
          np_warehouse_name: warehouse?.description,
          delivery_address: needsAddress ? address.trim() : undefined,

          payment_method: paymentMethod,
        },
        localeToApiLang[locale],
      );

      clearCart();

      // Онлайн-оплата — редірект до провайдера (LiqPay).
      if (order.payment_url) {
        window.location.href = order.payment_url;
        return;
      }

      // ⚠️ Роут по ПУБЛІЧНОМУ ТОКЕНУ, не по номеру.
      router.push(localePath(locale, `/order/${order.public_token}`));
    } catch (e) {
      // 409 = ціни змінились між preview і сабмітом. Не оформлюємо мовчки —
      // перезавантажуємо кошик і показуємо нову суму.
      if (e instanceof ApiError && e.status === 409) {
        toast.error(t("cart.priceChanged"), { description: t("cart.priceChangedHint") });
        void reload();
      } else if (e instanceof ApiError && e.status === 501) {
        // ⚠️ POST /orders на бекенді ще НЕ НАПИСАНИЙ (див. lib/api/http.ts).
        // Кошик НЕ чистимо і нікуди не редіректимо: краще чесне «недоступно»,
        // ніж «замовлення прийнято», якого насправді ніхто не отримав.
        toast.error(t("checkout.unavailable"), { description: t("checkout.unavailableHint") });
      } else {
        toast.error(t("common.error"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!hydrated) return <Skeleton className="h-96 w-full" />;

  // Ціни не завантажились — форму не показуємо взагалі. Чекаут з невідомою сумою
  // гірший за чесне «сервіс недоступний»: кошик при цьому лишається на місці.
  if (error && lines.length > 0) {
    return <ApiErrorState onRetry={reload} />;
  }

  if (lines.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-12 text-center">
        <p className="text-h3 text-foreground">{t("cart.empty")}</p>
        <Link
          href={localePath(locale, "/catalog")}
          className={cn(buttonVariants({ size: "xl" }), "mt-6")}
        >
          {t("cart.goToCatalog")}
        </Link>
      </div>
    );
  }

  return (
    <form
      className="grid gap-8 lg:grid-cols-[1fr_360px]"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="flex flex-col gap-8">
        {/* ── Контакти ────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <h2 className="text-h2 text-foreground">{t("checkout.contacts")}</h2>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label={t("checkout.lastName")}
              value={lastName}
              onChange={setLastName}
              error={errors.lastName}
              autoComplete="family-name"
              required
            />
            <Field
              label={t("checkout.firstName")}
              value={firstName}
              onChange={setFirstName}
              error={errors.firstName}
              autoComplete="given-name"
              required
            />
            <Field
              label={t("checkout.phone")}
              value={phone}
              onChange={setPhone}
              error={errors.phone}
              type="tel"
              autoComplete="tel"
              placeholder="+380 XX XXX XX XX"
              required
            />
            <Field
              label={t("checkout.email")}
              value={email}
              onChange={setEmail}
              error={errors.email}
              type="email"
              autoComplete="email"
              required={emailRequired}
              hint={emailRequired ? t("checkout.emailRequiredForOnline") : undefined}
            />
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">
              {t("checkout.comment")}
            </span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              className="rounded-md border border-input bg-background p-3 text-sm focus-visible:border-ring focus-visible:outline-none"
            />
          </label>
        </section>

        {/* ── Доставка ────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <h2 className="text-h2 text-foreground">{t("checkout.delivery")}</h2>

          <div className="flex flex-col gap-2">
            {DELIVERY_METHODS.map((method) => (
              <label
                key={method}
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-md border p-3 text-sm transition-colors",
                  deliveryMethod === method
                    ? "border-primary bg-brand-subtle text-brand-subtle-foreground"
                    : "border-border hover:border-input",
                )}
              >
                <input
                  type="radio"
                  name="delivery"
                  value={method}
                  checked={deliveryMethod === method}
                  onChange={() => setDeliveryMethod(method)}
                  className="accent-primary"
                />
                {t(`checkout.deliveryMethod.${method}` as "checkout.deliveryMethod.pickup")}
              </label>
            ))}
          </div>

          {needsNP && (
            <div className="flex flex-col gap-4">
              <div className="relative flex flex-col gap-1.5">
                <span className="text-sm font-medium text-foreground">
                  {t("checkout.city")} *
                </span>
                <input
                  type="text"
                  autoComplete="off"
                  value={city ? city.name : cityQuery}
                  onChange={(e) => {
                    setCity(null);
                    setCityQuery(e.target.value);
                  }}
                  placeholder={t("checkout.cityPlaceholder")}
                  className={cn(
                    "h-10 rounded-md border bg-background px-3 text-sm focus-visible:border-ring focus-visible:outline-none",
                    errors.city ? "border-destructive" : "border-input",
                  )}
                />
                {errors.city && (
                  <span className="text-xs text-destructive">{errors.city}</span>
                )}

                {cities.length > 0 && (
                  <ul className="absolute top-full z-20 mt-1 w-full overflow-hidden rounded-md border border-border bg-popover shadow-lg">
                    {cities.map((c) => (
                      <li key={c.ref}>
                        <button
                          type="button"
                          onClick={() => {
                            setCity(c);
                            setCityQuery(c.name);
                            setCities([]);
                          }}
                          className="flex w-full flex-col px-3 py-2 text-left hover:bg-accent"
                        >
                          <span className="text-sm text-foreground">{c.name}</span>
                          <span className="text-xs text-muted-foreground">{c.area}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {deliveryMethod !== "np_courier" && warehouses.length > 0 && (
                <label className="flex flex-col gap-1.5">
                  <span className="text-sm font-medium text-foreground">
                    {t("checkout.warehouse")}
                  </span>
                  <select
                    value={warehouseRef}
                    onChange={(e) => setWarehouseRef(e.target.value)}
                    className="h-10 rounded-md border border-input bg-background px-2 text-sm focus-visible:border-ring focus-visible:outline-none"
                  >
                    {warehouses
                      .filter((w) =>
                        deliveryMethod === "np_postomat"
                          ? w.service_type === "WarehousePostomat"
                          : w.service_type !== "WarehousePostomat",
                      )
                      .map((w) => (
                        <option key={w.ref} value={w.ref}>
                          {w.description || w.short_address}
                        </option>
                      ))}
                  </select>
                </label>
              )}
            </div>
          )}

          {needsAddress && (
            <Field
              label={t("checkout.address")}
              value={address}
              onChange={setAddress}
              error={errors.address}
              autoComplete="street-address"
              required
            />
          )}
        </section>

        {/* ── Оплата ──────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <h2 className="text-h2 text-foreground">{t("checkout.payment")}</h2>

          <div className="flex flex-col gap-2">
            {PAYMENT_METHODS.map((method) => {
              // Оплата частинами доступна тільки якщо ВСІ позиції її дозволяють.
              // Рахує сервер (data.installment_allowed), не фронт.
              const disabled = method === "installment" && !data?.installment_allowed;

              return (
                <label
                  key={method}
                  className={cn(
                    "flex cursor-pointer items-center gap-3 rounded-md border p-3 text-sm transition-colors",
                    paymentMethod === method
                      ? "border-primary bg-brand-subtle text-brand-subtle-foreground"
                      : "border-border hover:border-input",
                    disabled && "cursor-not-allowed opacity-50",
                  )}
                >
                  <input
                    type="radio"
                    name="payment"
                    value={method}
                    checked={paymentMethod === method}
                    disabled={disabled}
                    onChange={() => setPaymentMethod(method)}
                    className="accent-primary"
                  />
                  {t(`checkout.paymentMethod.${method}` as "checkout.paymentMethod.cod")}
                </label>
              );
            })}
          </div>
        </section>
      </div>

      {/* ── Підсумок ──────────────────────────────────────────────── */}
      <aside className="h-fit rounded-lg border border-border bg-card p-5 lg:sticky lg:top-32">
        <h2 className="text-h3 text-foreground">{t("checkout.yourOrder")}</h2>

        <ul className="mt-4 flex flex-col gap-3">
          {(data?.items ?? []).map((item) => (
            <li key={item.id} className="flex justify-between gap-3 text-sm">
              <span className="line-clamp-2 text-muted-foreground">
                {item.name}
                {item.qty > 1 && <span className="tnum"> × {item.qty}</span>}
              </span>
              <span className="shrink-0 font-medium text-foreground tnum">
                {formatPrice(item.line_total, locale)} ₴
              </span>
            </li>
          ))}
        </ul>

        <div className="mt-4 flex items-baseline justify-between border-t border-border pt-4">
          <span className="text-sm text-muted-foreground">{t("cart.total")}</span>
          <span className="price text-price-xl text-price-regular">
            {isLoading ? "…" : `${formatPrice(data?.subtotal ?? "0", locale)} ₴`}
          </span>
        </div>

        <Button type="submit" size="xl" className="mt-5 w-full" disabled={submitting}>
          {submitting ? t("checkout.submitting") : t("checkout.submit")}
        </Button>

        <p className="mt-3 text-xs text-muted-foreground">{t("checkout.agreement")}</p>
      </aside>
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  hint,
  type = "text",
  required,
  placeholder,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
  type?: string;
  required?: boolean;
  placeholder?: string;
  autoComplete?: string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-foreground">
        {label}
        {required && " *"}
      </span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        aria-invalid={Boolean(error)}
        className={cn(
          "h-10 rounded-md border bg-background px-3 text-sm focus-visible:border-ring focus-visible:outline-none",
          error ? "border-destructive" : "border-input",
        )}
      />
      {error ? (
        <span className="text-xs text-destructive">{error}</span>
      ) : hint ? (
        <span className="text-xs text-muted-foreground">{hint}</span>
      ) : null}
    </label>
  );
}

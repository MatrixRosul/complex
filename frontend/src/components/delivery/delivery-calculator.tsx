"use client";

import { useEffect, useRef, useState } from "react";
import { Package, Truck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { api } from "@/lib/api";
import { formatPackage, formatPrice } from "@/lib/format";
import type { DeliveryQuoteOut, NPCityOut, NPWarehouseOut, ProductDetail } from "@/lib/api/types";

/**
 * Калькулятор доставки (DESIGN_SYSTEM §4.8).
 *
 * ⚠️ Габарити й вагу НЕ ВВОДИМО РУКАМИ. Вони приходять з характеристик товару:
 * «Габарити упаковки (ВхШхГ)» і «Вага в упаковці» (INPUTS §3.4). Саме упаковки,
 * а не самого товару — Нова Пошта рахує по коробці.
 *
 * ⚠️ Ціна результату — НЕ акцентом. Це не ціна товару; якщо пофарбувати її
 * помаранчевим, акцент розмиється і перестане означати «тут покупка».
 *
 * ⚠️ Помилка розрахунку НЕ БЛОКУЄ покупку — показуємо «вартість уточнить менеджер».
 * Впав API НП — це наша проблема, а не покупця.
 */
export function DeliveryCalculator({ product }: { product: ProductDetail }) {
  const t = useT();
  const locale = useLocale();

  const [expanded, setExpanded] = useState(false);
  const [cityQuery, setCityQuery] = useState("");
  const [cities, setCities] = useState<NPCityOut[]>([]);
  const [city, setCity] = useState<NPCityOut | null>(null);
  /** ⚠️ Окремий стан пошуку міста — інакше «нічого не знайдено» не відрізнити від «ще шукаю». */
  const [citySearch, setCitySearch] = useState<"idle" | "searching" | "done">("idle");
  const [warehouses, setWarehouses] = useState<NPWarehouseOut[]>([]);
  const [warehouseRef, setWarehouseRef] = useState("");
  const [serviceType, setServiceType] = useState<"warehouse" | "courier">("warehouse");

  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [quote, setQuote] = useState<DeliveryQuoteOut | null>(null);

  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pkg = formatPackage(
    product.package_weight_kg,
    product.package_height_cm,
    product.package_width_cm,
    product.package_depth_cm,
    locale,
  );

  // Автокомпліт міста: дебаунс 300 мс, мінімум 2 символи.
  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    if (cityQuery.trim().length < 2 || city) {
      // Скидання/прапорець мусять спрацювати СИНХРОННО, до await: інакше між
      // зміною входу й відповіддю буде кадр зі старими даними.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCities([]);
      setCitySearch("idle");
      return;
    }

    let cancelled = false;
    setCitySearch("searching");

    debounce.current = setTimeout(() => {
      void api
        .searchCities(cityQuery)
        .then((list) => {
          if (cancelled) return;
          setCities(list);
          setCitySearch("done");
        })
        .catch(() => {
          if (cancelled) return;
          setCities([]);
          setCitySearch("done");
        });
    }, 300);

    return () => {
      cancelled = true;
      if (debounce.current) clearTimeout(debounce.current);
    };
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
    void api
      .getWarehouses(city.ref)
      .then((list) => {
        setWarehouses(list);
        setWarehouseRef(list[0]?.ref ?? "");
      })
      .catch(() => {
        setWarehouses([]);
        setWarehouseRef("");
      });
  }, [city]);

  const calculate = async () => {
    if (!city) return;

    setStatus("loading");
    try {
      /**
       * ⚠️ Габарити НЕ ШЛЕМО. Бекенд бере їх з товару сам (`effective_dims`) і вміє
       * фолбек на дефолти категорії — тому товар без заповнених характеристик тепер
       * теж рахується, а не впирається в «error», як було раніше.
       *
       * `service_type` для кур'єра — WarehouseDoors; для відділення/поштомата бекенд
       * підставить той, що прив'язаний до КОНКРЕТНОЇ точки (поштомат ≠ відділення,
       * різниця ~10 грн на замовлення).
       */
      const result = await api.quoteDelivery({
        settlement_ref: city.ref,
        warehouse_ref: serviceType === "warehouse" ? warehouseRef : "",
        service_type: serviceType === "courier" ? "WarehouseDoors" : "",
        product_id: product.id,
        qty: 1,
      });
      setQuote(result);
      // 🔴 Бекенд ЗАВЖДИ віддає 200: невдача — це `ok:false`, а не виняток.
      setStatus(result.ok ? "success" : "error");
    } catch {
      setStatus("error");
    }
  };

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="flex w-full items-center gap-2 rounded-lg border border-border p-4 text-left text-sm font-medium text-foreground transition-colors hover:border-input"
      >
        <Truck aria-hidden className="size-5 text-muted-foreground" />
        {t("delivery.calculate")}
      </button>
    );
  }

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Truck aria-hidden className="size-4 text-muted-foreground" />
        {t("delivery.title")}
      </h3>

      {/* ── Місто: автокомпліт ─────────────────────────────────────── */}
      <div className="relative">
        <label htmlFor="delivery-city" className="text-xs text-muted-foreground">
          {t("delivery.city")}
        </label>
        <input
          id="delivery-city"
          type="text"
          autoComplete="off"
          value={city ? city.name : cityQuery}
          onChange={(e) => {
            setCity(null);
            setQuote(null);
            setStatus("idle");
            setCityQuery(e.target.value);
          }}
          placeholder={t("checkout.cityPlaceholder")}
          className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:border-ring focus-visible:outline-none"
        />

        {cities.length > 0 && (
          <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border bg-popover shadow-lg">
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

        {/* ⚠️ Порожній результат МУСИТЬ мати свій стан. Раніше список просто не рендерився,
            і людина, що ввела назву з друкарською помилкою, бачила рівно НІЧОГО — не
            зрозуміти, чи пошук ще триває, чи міста не існує, чи все зламалось. */}
        {citySearch === "done" && !city && cities.length === 0 && (
          <p className="mt-1 text-xs text-muted-foreground">{t("delivery.cityNotFound")}</p>
        )}
        {citySearch === "searching" && !city && (
          <p className="mt-1 text-xs text-muted-foreground">{t("delivery.searching")}</p>
        )}
      </div>

      {/* ── Відділення / кур'єр ────────────────────────────────────── */}
      <div className="flex gap-1 rounded-md border border-border p-0.5">
        {(["warehouse", "courier"] as const).map((type) => (
          <button
            key={type}
            type="button"
            onClick={() => {
              setServiceType(type);
              setStatus("idle");
            }}
            className={cn(
              "flex-1 rounded-sm px-3 py-1.5 text-sm transition-colors",
              serviceType === type
                ? "bg-brand-subtle font-medium text-brand-subtle-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {type === "warehouse" ? t("delivery.toWarehouse") : t("delivery.toCourier")}
          </button>
        ))}
      </div>

      {serviceType === "warehouse" && city && (
        <label className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">{t("delivery.warehouse")}</span>
          {warehouses.length > 0 ? (
            <select
              value={warehouseRef}
              onChange={(e) => setWarehouseRef(e.target.value)}
              className="h-10 rounded-md border border-input bg-background px-2 text-sm focus-visible:border-ring focus-visible:outline-none"
            >
              {warehouses.map((w) => (
                <option key={w.ref} value={w.ref}>
                  {w.description || w.short_address}
                </option>
              ))}
            </select>
          ) : (
            /* Бекенд відсіює точки, куди посилка ФІЗИЧНО не влізе (холодильник у поштомат).
               Порожній список — це змістовна відповідь, а не збій. */
            <span className="text-xs text-muted-foreground">{t("delivery.noWarehouses")}</span>
          )}
        </label>
      )}

      <Button size="xl" disabled={!city || status === "loading"} onClick={calculate}>
        {status === "loading" ? t("delivery.loading") : t("delivery.calculate")}
      </Button>

      {/* ── Результат ──────────────────────────────────────────────── */}
      {status === "loading" && <Skeleton className="h-6 w-48" />}

      {status === "success" && quote?.cost && (
        <div className="flex flex-col gap-1">
          {/* Ціна доставки — text-h3, НЕ акцентом: це не ціна товару.
              Термінів (days) реальний API НП тут не віддає — тому їх і не показуємо,
              замість того щоб вигадувати «1–3 дні», як робив мок. */}
          <p className="text-h3 text-foreground tnum">
            {t("delivery.resultCost", { price: `${formatPrice(quote.cost, locale)} ₴` })}
          </p>
        </div>
      )}

      {/* ⚠️ Недоступний калькулятор НЕ БЛОКУЄ покупку (backend/delivery/api.py, шапка):
          показуємо «вартість повідомить менеджер», а не помилку на пів екрана. */}
      {status === "error" && (
        <p className="text-sm text-muted-foreground">{t("delivery.unavailable")}</p>
      )}

      {/* Що саме пішло в розрахунок — знімає половину дзвінків у підтримку. */}
      {pkg && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground tnum">
          <Package aria-hidden className="size-3.5" />
          {t("delivery.dimensions")}: {pkg}
        </p>
      )}

      <p className="text-xs text-muted-foreground">{t("delivery.disclaimer")}</p>
    </section>
  );
}

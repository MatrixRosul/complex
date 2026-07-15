import { PackageCheck, ShieldCheck, Store, Truck } from "lucide-react";

import { getT } from "@/i18n/dictionary";
import type { Locale } from "@/i18n/config";

/**
 * Смуга довіри: чому тут безпечно купувати.
 *
 * ⚠️ ТУТ ЛИШЕ ТЕ, ЩО ПРАВДА І ЩО ВЖЕ ПРАЦЮЄ. Жодних «знижок до 15%», «безкоштовної
 * доставки» чи «14 днів на повернення» — цього замовник не обіцяв, а вигадані обіцянки
 * на головній — це обман від імені реального магазину (за це вже знесено демо-банери).
 *
 * Кожен пункт спирається на факт із футера: гарантія від виробника, доставка Новою
 * Поштою / Meest / Delivery, самовивіз в Ужгороді, оплата при отриманні.
 *
 * Серверний компонент (`getT`, а не `useT`): блок статичний, і тягти заради нього
 * клієнтський JS немає сенсу.
 */
export function TrustStrip({ locale }: { locale: Locale }) {
  const t = getT(locale);

  const items = [
    { icon: ShieldCheck, title: t("trust.warranty"), text: t("trust.warrantyText") },
    { icon: Truck, title: t("trust.delivery"), text: t("trust.deliveryText") },
    { icon: Store, title: t("trust.pickup"), text: t("trust.pickupText") },
    { icon: PackageCheck, title: t("trust.payment"), text: t("trust.paymentText") },
  ];

  return (
    <section className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:grid-cols-2 lg:grid-cols-4">
      {items.map(({ icon: Icon, title, text }) => (
        <div key={title} className="flex items-start gap-3">
          <Icon aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-foreground">{title}</span>
            <span className="text-xs text-muted-foreground">{text}</span>
          </div>
        </div>
      ))}
    </section>
  );
}

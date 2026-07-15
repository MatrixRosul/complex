"use client";

import { Phone } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useT } from "@/i18n/provider";
import { PHONES } from "@/lib/site";
import type { ProductDetail } from "@/lib/api/types";

/**
 * «Купити в 1 клік» — ЧЕСНИЙ варіант, поки немає `POST /orders`.
 *
 * ⚠️ ЧОМУ НЕ ФОРМА З ПОЛЕМ «ТЕЛЕФОН».
 * Спокуса очевидна: показати інпут, зібрати номер, сказати «ми передзвонимо». Але роутера
 * замовлень на бекенді НЕМАЄ (`createOrder` чесно кидає 501), і подіти цей номер НІКУДИ —
 * він помер би в стейті вкладки. Це не «заглушка», це ОБІЦЯНКА, якої ніхто не виконає:
 * людина чекає дзвінка, якого не буде. Мовчазний no-op поганий, але фальшива форма — гірша,
 * бо створює хибну впевненість, що замовлення прийнято.
 *
 * Тому модалка не ІМІТУЄ оформлення, а дає СПРАВЖНІЙ спосіб купити просто зараз: реальні
 * телефони магазину (tel: — на мобільному це один тап до дзвінка) і код товару, який
 * менеджер вб'є в свою систему. Це працює вже сьогодні, нічого не вигадує і не бреше.
 *
 * Коли з'явиться `POST /orders` — сюди приїде справжня форма, і саме цей компонент
 * стане єдиним місцем зміни.
 */
export function OneClickDialog({
  product,
  open,
  onOpenChange,
}: {
  product: ProductDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("product.oneClickTitle")}</DialogTitle>
          <DialogDescription>{t("product.oneClickText")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          {/* Код товару — те, що менеджер попросить першим. */}
          <div className="rounded-lg border border-border bg-muted/50 p-3">
            <p className="text-xs text-muted-foreground">{t("product.oneClickCode")}</p>
            <p className="product-code mt-1 text-lg font-semibold text-foreground">
              {product.sku}
            </p>
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{product.name}</p>
          </div>

          {/* tel: — на телефоні це один тап до дзвінка, а не «перепишіть номер вручну». */}
          <div className="flex flex-col gap-2">
            {PHONES.map((phone) => (
              <a
                key={phone}
                href={`tel:${phone}`}
                className="flex items-center gap-2 rounded-md border border-border px-3 py-2.5 text-sm font-medium text-foreground transition-colors hover:border-input hover:bg-accent"
              >
                <Phone aria-hidden className="size-4 text-muted-foreground" />
                {phone}
              </a>
            ))}
          </div>

          <p className="text-xs text-muted-foreground">{t("product.oneClickHours")}</p>
        </div>
      </DialogContent>
    </Dialog>
  );
}

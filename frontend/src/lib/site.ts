import type { ApiLang, ContactsOut } from "./api/types";

/**
 * Дані сайту, яких ЩЕ НЕМАЄ в API.
 *
 * ⚠️ Це не «моки». Це реальні контакти замовника з INPUTS §1 (той самий рядок, що зараз
 * стоїть на galiton). GET /cms/contacts на бекенді не написаний (404), а хедер і футер
 * без телефону — це втрачені дзвінки. Коли ендпоінт з'явиться, `httpApi.getContacts()`
 * візьме дані з нього, і цей фолбек просто перестане викликатись.
 */

/** Реальні телефони замовника (INPUTS §1). Експортовані: їх показує «Купити в 1 клік». */
export const PHONES = ["+380950915222", "+380991717925", "+380507388811"];

export const FALLBACK_CONTACTS = (lang: ApiLang): ContactsOut => ({
  phones: PHONES,
  email: "uzh.tehnika77@gmail.com",
  address: lang === "uk" ? "м. Ужгород" : "г. Ужгород",
  working_hours: [
    { days: lang === "uk" ? "ПН – ПТ" : "ПН – ПТ", time: "09:00 – 20:00" },
    { days: lang === "uk" ? "СБ – НД" : "СБ – ВС", time: "10:00 – 17:00" },
  ],
});

/**
 * Куди кладемо статичну сторінку в меню, якщо MenuItem у БД ще не заведений.
 * Ключ — slug сторінки з /cms/pages.
 */
export const PAGE_MENU_BLOCK: Record<string, string> = {
  "payment-delivery": "buyers",
  warranty: "buyers",
  about: "info",
  contacts: "info",
};

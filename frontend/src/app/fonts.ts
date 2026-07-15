import localFont from "next/font/local";

/**
 * Inter Variable, self-hosted (DESIGN_SYSTEM §3).
 *
 * Чому Inter: якісна кирилиця з першого дня (і, ї, є, ґ, апостроф — саме на них
 * ламаються дешеві гротески) + tabular numerals (tnum), критичні для цін у сітці.
 * Файл — офіційний реліз rsms/inter v4.1, повний (усі сабсети, включно з кирилицею).
 *
 * ⚠️ Без CDN і без next/font/google: .woff2 лежить у репозиторії, тож білд не
 * залежить від мережі, а рантайм — від сторонніх доменів.
 */
export const inter = localFont({
  src: [
    { path: "./fonts/InterVariable.woff2", weight: "100 900", style: "normal" },
    { path: "./fonts/InterVariable-Italic.woff2", weight: "100 900", style: "italic" },
  ],
  variable: "--font-sans",
  display: "swap",
  preload: true,
  fallback: ["system-ui", "Segoe UI", "Roboto", "sans-serif"],
});

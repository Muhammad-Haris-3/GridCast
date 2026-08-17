import type { Metadata } from "next";
import Link from "next/link";
import {
  Instrument_Serif,
  Instrument_Sans,
  JetBrains_Mono,
  Bricolage_Grotesque,
  Public_Sans,
  IBM_Plex_Mono,
} from "next/font/google";
import NavLinks from "./NavLinks";
import ThemeToggle, { THEME_INIT } from "./ThemeToggle";
import "./globals.css";

// Both themes' fonts are declared here and globals.css picks a trio per theme
// through --font-display/body/mono. Only the dark trio is preloaded: preload
// injects a <link rel="preload"> that downloads the file whether or not anything
// uses it, and the inactive theme's fonts are not used until someone switches.
// Dropping preload keeps the @font-face, so the browser fetches them on switch.

// Instrument Serif ships a single weight; the sans and mono are variable.
const display = Instrument_Serif({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-instrument-serif",
});

const body = Instrument_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-instrument-sans",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

const displayLight = Bricolage_Grotesque({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-bricolage",
  preload: false,
});

const bodyLight = Public_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-public-sans",
  preload: false,
});

// IBM Plex Mono is not a variable font, so its weights are enumerated.
const monoLight = IBM_Plex_Mono({
  weight: ["400", "500", "600"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-plex-mono",
  preload: false,
});

const FONT_VARS = [
  display.variable,
  body.variable,
  mono.variable,
  displayLight.variable,
  bodyLight.variable,
  monoLight.variable,
].join(" ");

export const metadata: Metadata = {
  title: "GridCast — forecasts that grade themselves",
  description:
    "Half-hourly GB grid carbon intensity forecasts, published before the outcome exists and scored automatically once the actual arrives.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // data-theme is rendered as "dark" rather than left off: every rule in
    // globals.css is scoped to a theme, so an unset attribute would serve an
    // unstyled page to anyone without JavaScript. THEME_INIT upgrades it from
    // localStorage during parse, before the body paints.
    //
    // data-scroll-behavior is required in Next 16: without it the framework no
    // longer neutralises `scroll-behavior: smooth` during route transitions, so
    // every nav click animates a scroll to top instead of jumping.
    // suppressHydrationWarning covers exactly one intended mismatch: THEME_INIT
    // rewrites data-theme before React hydrates, so the server's "dark" and the
    // live "light" disagree by design. React does not revert the attribute, so
    // without this the only symptom is a hydration error logged on every
    // light-mode load — noise that buries real ones. It suppresses this element
    // only, not its children.
    <html
      lang="en-GB"
      data-theme="dark"
      data-scroll-behavior="smooth"
      className={FONT_VARS}
      suppressHydrationWarning
    >
      <body>
        {/* Not next/script: beforeInteractive does not block paint, which is the
            one thing this has to do. Inline and first in the body, it runs while
            the rest of the document is still being parsed. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        <header className="site-header">
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <Link href="/" className="brand">
              Grid<span>Cast</span>
            </Link>
            <span className="header-badge">GB grid · 48 h</span>
          </div>
          <NavLinks />
          <div className="header-tail">
            <div className="header-status">
              <i />
              <span>pipeline live</span>
            </div>
            <ThemeToggle />
          </div>
        </header>
        <main>{children}</main>
        <footer>
          <p>
            Carbon intensity data from the National Grid ESO Carbon Intensity
            API, licensed CC BY 4.0. Demand and price from Elexon BMRS. Weather
            from Open-Meteo.
          </p>
        </footer>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import Link from "next/link";
import { Instrument_Serif, Instrument_Sans, JetBrains_Mono } from "next/font/google";
import NavLinks from "./NavLinks";
import "./globals.css";

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

export const metadata: Metadata = {
  title: "GridCast — forecasts that grade themselves",
  description:
    "Half-hourly GB grid carbon intensity forecasts, published before the outcome exists and scored automatically once the actual arrives.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // data-scroll-behavior is required in Next 16: without it the framework no
    // longer neutralises `scroll-behavior: smooth` during route transitions,
    // so every nav click animates a scroll to top instead of jumping.
    <html
      lang="en-GB"
      data-scroll-behavior="smooth"
      className={`${display.variable} ${body.variable} ${mono.variable}`}
    >
      <body>
        <header className="site-header">
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <Link href="/" className="brand">
              Grid<span>Cast</span>
            </Link>
            <span className="header-badge">GB grid · 48 h</span>
          </div>
          <NavLinks />
          <div className="header-status">
            <i />
            <span>pipeline live</span>
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

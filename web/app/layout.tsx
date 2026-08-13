import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "GridCast — forecasts that grade themselves",
  description:
    "Half-hourly GB grid carbon intensity forecasts, published before the outcome exists and scored automatically once the actual arrives.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-GB">
      <body>
        <header className="site-header">
          <Link href="/" className="brand">
            Grid<span>Cast</span>
          </Link>
          <nav>
            <Link href="/">Forecast</Link>
            <Link href="/plan">Plan</Link>
            <Link href="/accuracy">Accuracy</Link>
            <Link href="/status">Status</Link>
            <Link href="/about">About</Link>
          </nav>
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

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Forecast" },
  { href: "/plan", label: "Plan" },
  { href: "/accuracy", label: "Accuracy" },
  { href: "/status", label: "Status" },
  { href: "/about", label: "About" },
];

/** The nav pill. Client-side only because it needs the current path to mark the
 *  active tab — aria-current drives both the styling and the screen reader. */
export default function NavLinks() {
  const pathname = usePathname();
  return (
    <nav>
      {LINKS.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          aria-current={pathname === href ? "page" : undefined}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}

"use client";

export type Theme = "dark" | "light";

const KEY = "gridcast-theme";

/**
 * Runs inline in the document, before the body paints — see layout.tsx.
 *
 * The server cannot know which theme this visitor chose, so it renders dark and
 * this corrects it during parse. Doing the same work in an effect would paint
 * dark first and then flip, which is the flash every theme switcher is judged on.
 *
 * A stored choice wins over the OS preference: someone who has picked light on a
 * dark-set machine meant it. With nothing stored, the OS decides.
 */
export const THEME_INIT = `(function(){try{
var s=localStorage.getItem(${JSON.stringify(KEY)});
if(s!=="dark"&&s!=="light"){s=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";}
document.documentElement.dataset.theme=s;
}catch(e){}})();`;

/**
 * Which label and icon show is decided by CSS from the root's data-theme, not by
 * React state. State would start at the server's guess and correct itself after
 * hydration, so a light-mode reader would watch the button say "Light" and then
 * flip to "Dark" — the flash moved from the page onto the control. Both labels
 * are in the markup and the inactive one is display:none, which also means a
 * screen reader announces only the live one, so no aria-label is needed.
 */
export default function ThemeToggle() {
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => {
        const root = document.documentElement;
        const next: Theme = root.dataset.theme === "light" ? "dark" : "light";
        root.dataset.theme = next;
        try {
          localStorage.setItem(KEY, next);
        } catch {
          // Private browsing can refuse writes. The theme still applies to this
          // page; it just will not be remembered, which beats throwing.
        }
      }}
    >
      <span className="theme-when-dark">
        <svg
          viewBox="0 0 24 24"
          width="15"
          height="15"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6" />
        </svg>
        Light
      </span>
      <span className="theme-when-light">
        <svg
          viewBox="0 0 24 24"
          width="15"
          height="15"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M20.5 14.6A8.6 8.6 0 1 1 9.4 3.5a6.9 6.9 0 0 0 11.1 11.1Z" />
        </svg>
        Dark
      </span>
    </button>
  );
}

export type ThemeMode = "dark" | "light";

const STORAGE_KEY = "theme";
const SYSTEM_SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

const DARK_THEME_VARS: Record<string, string> = {
  "--foreground": "color-mix(in srgb, #ffffff 0%, transparent)",
  "--foreground-base": "#ffffff",
  "--foreground-alpha": "0",
  "--midground": "color-mix(in srgb, #ffe6cb 100%, transparent)",
  "--midground-base": "#ffe6cb",
  "--midground-alpha": "1",
  "--background": "color-mix(in srgb, #041c1c 100%, transparent)",
  "--background-base": "#041c1c",
  "--background-alpha": "1",
  "--warm-glow": "rgba(255, 189, 56, 0.35)",
  "--noise-opacity-mul": "1",
  "--theme-font-sans": SYSTEM_SANS,
  "--theme-font-display": SYSTEM_SANS,
  "--theme-letter-spacing": "-0.022em",
  "--color-foreground": "var(--midground)",
  "--color-card": "color-mix(in srgb, var(--midground-base) 4%, var(--background-base))",
  "--color-card-foreground": "var(--midground)",
  "--color-primary": "#0071e3",
  "--color-primary-foreground": "#ffffff",
  "--color-secondary": "color-mix(in srgb, var(--midground-base) 6%, var(--background-base))",
  "--color-secondary-foreground": "var(--midground)",
  "--color-muted": "color-mix(in srgb, var(--midground-base) 8%, var(--background-base))",
  "--color-muted-foreground": "var(--color-text-secondary)",
  "--color-text-secondary": "color-mix(in srgb, var(--midground-base) 80%, transparent)",
  "--color-text-tertiary": "color-mix(in srgb, var(--midground-base) 60%, transparent)",
  "--color-accent": "color-mix(in srgb, var(--midground-base) 10%, var(--background-base))",
  "--color-accent-foreground": "var(--midground)",
  "--color-border": "color-mix(in srgb, var(--midground-base) 15%, transparent)",
  "--color-input": "color-mix(in srgb, var(--midground-base) 15%, transparent)",
  "--color-ring": "#0071e3",
  "--color-popover": "color-mix(in srgb, var(--midground-base) 4%, var(--background-base))",
  "--color-popover-foreground": "var(--midground)",
};

const LIGHT_THEME_VARS: Record<string, string> = {
  "--foreground": "color-mix(in srgb, #1d1d1f 100%, transparent)",
  "--foreground-base": "#1d1d1f",
  "--foreground-alpha": "1",
  "--midground": "color-mix(in srgb, #1d1d1f 100%, transparent)",
  "--midground-base": "#1d1d1f",
  "--midground-alpha": "1",
  "--background": "color-mix(in srgb, #f5f5f7 100%, transparent)",
  "--background-base": "#f5f5f7",
  "--background-alpha": "1",
  "--warm-glow": "rgba(0, 113, 227, 0.16)",
  "--noise-opacity-mul": "0.35",
  "--theme-font-sans": SYSTEM_SANS,
  "--theme-font-display": SYSTEM_SANS,
  "--theme-letter-spacing": "-0.022em",
  "--color-foreground": "#1d1d1f",
  "--color-card": "#ffffff",
  "--color-card-foreground": "#1d1d1f",
  "--color-primary": "#0071e3",
  "--color-primary-foreground": "#ffffff",
  "--color-secondary": "#e8e8ed",
  "--color-secondary-foreground": "#1d1d1f",
  "--color-muted": "#ededf0",
  "--color-muted-foreground": "#6e6e73",
  "--color-text-secondary": "#6e6e73",
  "--color-text-tertiary": "#86868b",
  "--color-accent": "#e8f2ff",
  "--color-accent-foreground": "#0071e3",
  "--color-border": "#d2d2d7",
  "--color-input": "#d2d2d7",
  "--color-ring": "#0071e3",
  "--color-popover": "#ffffff",
  "--color-popover-foreground": "#1d1d1f",
};

function isThemeMode(value: string | null): value is ThemeMode {
  return value === "dark" || value === "light";
}

function readStoredTheme(): ThemeMode | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isThemeMode(stored) ? stored : null;
  } catch {
    return null;
  }
}

function writeStoredTheme(theme: ThemeMode) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
}

export function getInitialTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";

  const stored = readStoredTheme();
  if (stored) return stored;

  const media = window.matchMedia?.("(prefers-color-scheme: light)");
  return media?.matches ? "light" : "dark";
}

export function applyTheme(theme: ThemeMode): ThemeMode {
  if (typeof document === "undefined") return theme;

  const root = document.documentElement;
  const vars = theme === "light" ? LIGHT_THEME_VARS : DARK_THEME_VARS;
  root.setAttribute("data-theme", theme);
  root.style.setProperty("color-scheme", theme);
  for (const [name, value] of Object.entries(vars)) {
    root.style.setProperty(name, value);
  }
  return theme;
}

export function toggleTheme(): ThemeMode {
  const current =
    typeof document === "undefined"
      ? getInitialTheme()
      : document.documentElement.getAttribute("data-theme");
  const next: ThemeMode = current === "light" ? "dark" : "light";
  applyTheme(next);
  writeStoredTheme(next);

  return next;
}

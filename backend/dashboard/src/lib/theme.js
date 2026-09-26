import { useCallback, useEffect, useState } from 'react';

const KEY = 'pharma-theme';
const META = { light: '#fafafa', dark: '#0a0a0a' };

function systemTheme() {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function storedTheme() {
  try {
    const t = localStorage.getItem(KEY);
    return t === 'light' || t === 'dark' ? t : null;
  } catch {
    return null;
  }
}

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META[theme]);
}

/** Light/dark theme. Follows the OS until the user picks one, then remembers it. */
export function useTheme() {
  const [theme, setTheme] = useState(() => storedTheme() || systemTheme());

  useEffect(() => apply(theme), [theme]);

  useEffect(() => {
    if (storedTheme()) return undefined;
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    const onChange = () => !storedTheme() && setTheme(systemTheme());
    mq?.addEventListener('change', onChange);
    return () => mq?.removeEventListener('change', onChange);
  }, []);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* storage unavailable: the choice lasts for this page only */
      }
      return next;
    });
  }, []);

  return [theme, toggle];
}

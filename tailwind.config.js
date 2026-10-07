// borderSubtle／borderLight 帶預設透明度（DESIGN_SYSTEM.md：黑／暖白 8%、14%）。
// 用 '<alpha-value>' 字串時，沒寫 /N 的 border-borderSubtle 會是 100%——淺色純黑框、
// 深色純白框。函式版：沒寫 /N 時 Tailwind 傳 var(--tw-*-opacity, 1)，改用預設透明度；
// 寫了 /N（border-borderSubtle/10）照 N。
const withDefaultAlpha = (cssVar, defaultAlpha) => ({ opacityValue }) => {
  const alpha =
    opacityValue === undefined || String(opacityValue).startsWith('var(')
      ? defaultAlpha
      : opacityValue;
  return `rgb(var(${cssVar}) / ${alpha})`;
};

/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './web/**/*.html',
    './web/**/*.js',
  ],
  // safelist: JS 動態拼接的 class（如 skill/memory toggle 的 peer-checked 變體）
  // 若只用字串拼接、Tailwind JIT 掃描不到，會漏編譯。這裡強制保留。
  safelist: [
    'peer-checked:bg-primary',
    'peer-checked:bg-primary/60',
    'peer-checked:translate-x-4',
    'peer-checked:bg-white',
  ],
  theme: {
    extend: {
      colors: {
        background: 'rgb(var(--color-background) / <alpha-value>)',
        surface: 'rgb(var(--color-surface) / <alpha-value>)',
        surfaceHighlight: 'rgb(var(--color-surface-highlight) / <alpha-value>)',
        primary: 'rgb(var(--color-primary) / <alpha-value>)',
        secondary: 'rgb(var(--color-secondary) / <alpha-value>)',
        accent: 'rgb(var(--color-accent) / <alpha-value>)',
        success: 'rgb(var(--color-success) / <alpha-value>)',
        danger: 'rgb(var(--color-danger) / <alpha-value>)',
        textMain: 'rgb(var(--color-text-primary) / <alpha-value>)',
        textMuted: 'rgb(var(--color-text-muted) / <alpha-value>)',
        textSecondary: 'rgb(var(--color-text-secondary) / <alpha-value>)',
        border: 'rgb(var(--color-border) / <alpha-value>)',
        borderSubtle: withDefaultAlpha('--color-border-subtle', 0.08),
        borderLight: withDefaultAlpha('--color-border-light', 0.14),
      },
      // CJK fallback：Inter / Inter Tight 是純拉丁字體（無中文字形），
      // 中文需顯式 fallback 到高品質繁中 sans，否則會跑出系統明體（serif）。
      // 拉丁字仍優先 Inter / Inter Tight；只有 CJK 字元才落到 Noto Sans TC。
      fontFamily: {
        serif: ['"Inter Tight"', '"Noto Sans TC"', '"PingFang TC"', '"Microsoft JhengHei"', 'system-ui', 'sans-serif'],
        sans: ['Inter', '"Noto Sans TC"', '"PingFang TC"', '"Microsoft JhengHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      letterSpacing: {
        tightest: '-0.04em',
        tighter: '-0.03em',
        tightdisplay: '-0.02em',
      },
      borderRadius: {
        '4xl': '2rem',
      },
      // 預設刻度（0、5、10…）沒有的透明度：頁面上用了 bg-primary/12、border-white/8、
      // bg-background/94 等，以前根本沒編出來（等於沒寫）
      opacity: {
        8: '0.08',
        12: '0.12',
        18: '0.18',
        94: '0.94',
        96: '0.96',
      },
      animation: {
        'spin-slow': 'spin 20s linear infinite',
      },
    },
  },
  plugins: [],
};

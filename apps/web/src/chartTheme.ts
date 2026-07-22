/** Shared ECharts fragments so every chart reads as one system. */

export const CHART_COLORS = {
  accent: '#3b82f6',
  accentHi: '#60a5fa',
  cyan: '#22d3ee',
  up: '#34d399',
  down: '#f87171',
  warn: '#fbbf24',
  purple: '#a78bfa',
  slate: '#94a3b8',
  text2: '#9aa7c4',
  text3: '#5f6c8c',
  line1: 'rgba(148,163,198,0.10)',
  line2: 'rgba(148,163,198,0.18)',
}

export const SERIES_PALETTE = [
  CHART_COLORS.cyan,
  CHART_COLORS.accent,
  CHART_COLORS.purple,
  CHART_COLORS.warn,
  CHART_COLORS.up,
  CHART_COLORS.down,
  '#93c5fd',
  '#f0abfc',
]

export const tooltipStyle = {
  backgroundColor: 'rgba(14,21,38,0.96)',
  borderColor: 'rgba(148,163,198,0.22)',
  borderWidth: 1,
  padding: [8, 12],
  textStyle: {
    color: '#eef2fa',
    fontSize: 11,
    fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
  },
  extraCssText: 'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.45);',
}

export function categoryAxis(data: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    type: 'category',
    data,
    axisLine: { lineStyle: { color: CHART_COLORS.line2 } },
    axisTick: { show: false },
    axisLabel: {
      color: CHART_COLORS.text3,
      fontSize: 10,
      fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
    },
    ...overrides,
  }
}

export function valueAxis(overrides: Record<string, unknown> = {}) {
  return {
    type: 'value',
    axisLabel: {
      color: CHART_COLORS.text3,
      fontSize: 10,
      fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
    },
    splitLine: { lineStyle: { color: CHART_COLORS.line1 } },
    ...overrides,
  }
}

export function areaGradient(hex: string, from = 0.28) {
  return {
    type: 'linear',
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: hexToRgba(hex, from) },
      { offset: 1, color: hexToRgba(hex, 0) },
    ],
  }
}

export function hexToRgba(hex: string, alpha: number): string {
  const value = hex.replace('#', '')
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

/** Neon line style: colored stroke with a soft same-hue glow. */
export function glowLine(hex: string, width = 1.8) {
  return {
    width,
    color: hex,
    shadowColor: hexToRgba(hex, 0.45),
    shadowBlur: 9,
    shadowOffsetY: 3,
  }
}

export function seriesColor(slot: number): string {
  return `var(--series-${((slot - 1) % 8) + 1})`;
}

export function Swatch({ slot }: { slot: number }) {
  return <span className="swatch" style={{ background: seriesColor(slot) }} aria-hidden="true" />;
}

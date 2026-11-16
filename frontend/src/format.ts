const counts = new Intl.NumberFormat("en-US");

export function seconds(value: number): string {
  return `${value.toPrecision(3)} s`;
}

export function axisSeconds(value: number): string {
  return `${Number(value.toPrecision(3))} s`;
}

export function ratio(value: number): string {
  return `${value.toFixed(2)} ×`;
}

export function share(value: number): string {
  return `${Math.round(value * 100)} %`;
}

export function count(value: number): string {
  return counts.format(value);
}

export function quality(value: number): string {
  return value.toFixed(4);
}

export function scientific(value: number): string {
  return value.toExponential(1);
}

export function gigabytes(bytes: number): string {
  return `${Math.round(bytes / 2 ** 30)} GB`;
}

export function decimal(value: number, digits: number): string {
  return value.toFixed(digits);
}

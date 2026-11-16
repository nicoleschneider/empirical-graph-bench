export type Selection = {
  hardware: string[];
  tasks: string[];
  datasets: string[];
  configurations: string[];
  colors: Record<string, number>;
  repetitions: number;
};

export const BASE_SEED = 123;

export function configurationKey(hardware: string, implementation: string, representation: string): string {
  return `${hardware}/${implementation}/${representation}`;
}

export function splitKey(key: string): { hardware: string; implementation: string; representation: string } {
  const [hardware, implementation, representation] = key.split("/");
  return { hardware, implementation, representation };
}

export function withColor(colors: Record<string, number>, key: string): Record<string, number> {
  if (key in colors) return colors;
  return { ...colors, [key]: Math.max(0, ...Object.values(colors)) + 1 };
}

export function toggle(values: string[], value: string, on: boolean): string[] {
  return on ? [...values.filter((v) => v !== value), value] : values.filter((v) => v !== value);
}

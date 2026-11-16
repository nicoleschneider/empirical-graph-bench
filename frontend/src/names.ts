import type { ConfigKey, Registry } from "./api";

export function keyId(key: ConfigKey): string {
  return `${key.hardware}/${key.implementation}/${key.representation}`;
}

export function sameKey(a: ConfigKey, b: ConfigKey): boolean {
  return keyId(a) === keyId(b);
}

export function configName(registry: Registry, implementation: string, representation: string): string {
  const entry = registry.implementations[implementation];
  if (entry.variants.length < 2) return entry.name;
  return `${entry.name}, ${registry.representations[representation].name}`;
}

export function keyName(registry: Registry, key: ConfigKey, hardwareCount: number): string {
  const name = configName(registry, key.implementation, key.representation);
  return hardwareCount > 1 ? `${key.hardware} ${name}` : name;
}

export function metricName(registry: Registry, metric: string): string {
  return metric === "etl_share" ? "ETL share" : registry.metrics[metric]?.name ?? metric;
}

export function metricUnit(registry: Registry, metric: string): string {
  return metric === "etl_share" ? "%" : registry.metrics[metric]?.unit ?? "";
}

export function taskOf(registry: Registry, implementation: string): string {
  return registry.algorithms[registry.implementations[implementation].algorithm].task;
}

export function gaibTaskName(registry: Registry, gaibTask: string): string {
  for (const implementation of Object.values(registry.implementations)) {
    const variant = implementation.variants.find((v) => v.gaib_task === gaibTask);
    if (variant) return configName(registry, implementation.id, variant.representation);
  }
  return gaibTask;
}

export function runDescription(registry: Registry, row: Record<string, string>): string {
  return `${gaibTaskName(registry, row.task)}, ${row.dataset}, seed ${row.seed}`;
}

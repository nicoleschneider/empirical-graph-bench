export type Task = { id: string; name: string; output: "communities" | "scores" };
export type Algorithm = { id: string; name: string; task: string; precision_sensitive: boolean };
export type Implementation = { id: string; name: string; algorithm: string; backend: string };
export type Representation = { id: string; name: string; precision: string };
export type Dataset = { id: string; name: string; directed: boolean; vertices: number; edges: number };

export type Registry = {
  tasks: Record<string, Task>;
  algorithms: Record<string, Algorithm>;
  implementations: Record<string, Implementation>;
  representations: Record<string, Representation>;
  datasets: Record<string, Dataset>;
};

export type DatasetProperties = {
  vertices: number;
  edges: number;
  degree_mean: number;
  degree_max: number;
  average_clustering: number;
  clustering_method: string;
};

export type DatasetStatus = {
  id: string;
  state: "missing" | "unverified" | "verified" | "mismatch";
  ground_truth: boolean;
  properties: DatasetProperties | null;
};

export type CoreGroup = { name: string; physical_cores: number };
export type Gpu = { name: string; driver: string; memory: string };

export type Hardware = {
  id: string;
  label: string;
  runner: string;
  specs: {
    model: string | null;
    cpu: string;
    physical_cores: number | null;
    core_groups: CoreGroup[];
    memory_bytes: number | null;
    os: string;
    os_version: string;
    machine: string;
    gpus: Gpu[];
    cuda_version: string | null;
  };
  capabilities: string[];
  unavailable: Record<string, string>;
};

export type ConfigurationStatus = {
  implementation: string;
  representation: string;
  algorithm: string;
  task: string;
  gaib_task: string;
  runnable: boolean;
  reasons: string[];
};

export type JobSpec = { id: string };

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${path}`);
  }
  return (await response.json()) as T;
}

export async function post<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, { method: "POST" });
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.detail ?? `${response.status} ${path}`);
  }
  return (await response.json()) as T;
}

export function whenJobEnds(jobId: string, onEnd: (status: string) => void): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  for (const status of ["finished", "failed", "cancelled"]) {
    source.addEventListener(status, () => {
      source.close();
      onEnd(status);
    });
  }
  return () => source.close();
}

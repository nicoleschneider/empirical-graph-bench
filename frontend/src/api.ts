export type Task = { id: string; name: string; output: "communities" | "scores" };
export type Algorithm = { id: string; name: string; task: string; precision_sensitive: boolean };
export type Variant = { representation: string; gaib_task: string };
export type Implementation = { id: string; name: string; algorithm: string; backend: string; variants: Variant[] };
export type Metric = { id: string; name: string; unit: string; better: string; tasks: string[]; requires_ground_truth: boolean };
export type Representation = { id: string; name: string; precision: string };
export type Dataset = { id: string; name: string; directed: boolean; vertices: number; edges: number };

export type Registry = {
  tasks: Record<string, Task>;
  algorithms: Record<string, Algorithm>;
  implementations: Record<string, Implementation>;
  representations: Record<string, Representation>;
  datasets: Record<string, Dataset>;
  metrics: Record<string, Metric>;
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
  runner: "local" | "ssh" | "local-shell";
  host: string | null;
  status: "reachable" | "checking" | "unavailable";
  reason: string | null;
  supports: string[] | null;
  specs: null | {
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
  hardware: string;
  implementation: string;
  representation: string;
  algorithm: string;
  task: string;
  gaib_task: string;
  runnable: boolean;
  reasons: string[];
};

export type ConfigKey = { hardware: string; implementation: string; representation: string };

export type Candidate = { id: string; hardware: string; implementation: string; representation: string; color_slot: number };

export type SessionConfig = {
  tasks: string[];
  datasets: string[];
  candidates: Candidate[];
  exploratory: string[];
  metrics: string[];
  repetitions: number;
  base_seed: number;
};

export type Session = {
  meta: { id: string; name: string; question: string; created_at: string };
  config: SessionConfig;
};

export type Prediction = {
  metric: string;
  subject: ConfigKey;
  relation: "<" | ">";
  reference: ConfigKey | null;
  threshold: number | null;
  datasets: string[];
};

export type LogEntry = {
  seq: number;
  id: string;
  recorded_at: string;
  kind: string;
  text: string;
  iteration: number;
  refers_to: string[];
  prediction: Prediction | null;
  source: string;
  suggestion: Record<string, unknown> | null;
  details: Record<string, unknown> | null;
  state_hash: string;
};

export type JobItem = {
  configuration: string;
  implementation: string;
  representation: string;
  dataset: string;
  gaib_task: string;
};

export type QueueView = { running: string | null; queued: string[] };

export type JobSpec = {
  id: string;
  kind: string;
  hypothesis: string | null;
  baseline: string | null;
  hardware: string;
  hardware_label: string;
  repetitions: number | null;
  datasets: string[];
  items: JobItem[];
  created_at: string;
};

export type JobView = { spec: JobSpec; status: string | null };

export type Spread = { median: number; min: number; max: number };

export type Summary = {
  config: ConfigKey;
  task: string;
  algorithm: string;
  dataset: string;
  runs: number;
  etl_s: Spread;
  processing_s: Spread;
  end_to_end_s: Spread;
  etl_share: number;
  effectiveness: Record<string, Spread>;
};

export type Change = {
  kind: string;
  source: ConfigKey | null;
  target: ConfigKey | null;
  datasets: string[];
};

export type PredictionDraft = { text: string; prediction: Prediction | null; note: string | null };
export type ValidityCheckDraft = { text: string; check: string; expected: string };

export type Suggestion = {
  key: string;
  rank: number;
  rule: string;
  source: string;
  hypothesis: string;
  change: Change;
  predictions: PredictionDraft[];
  validity_checks: ValidityCheckDraft[];
  caveats: string[];
  observation: string | null;
  dimension: string | null;
  rationale: string | null;
  repairs: string[];
};

export type SuggestionRecord = {
  id: string;
  response: {
    suggester: string;
    status: "complete" | "unavailable";
    message: string | null;
    suggestions: Suggestion[];
    fallback_reason: string | null;
    rejected: { hypothesis: string; reasons: string[] }[];
    calls: { model: string; latency_seconds: number; cost_usd: number }[];
  };
};

export type Interval = { point: number; low: number; high: number; resamples: number; seed: number };

export type DatasetEvaluation = {
  dataset: string;
  status: "held" | "refuted" | "narrow" | "no_data";
  subject_median: number | null;
  threshold: number | null;
  ratio: Interval | null;
  subject_runs: number;
  reference_runs: number | null;
};

export type Evaluation = {
  status: "held" | "refuted" | "narrow" | "pending";
  datasets: DatasetEvaluation[];
};

export type Check = { name: string; value: number | boolean | null; expected: string; passed: boolean | null };

export type ValidityResult = {
  targeted: ConfigKey;
  baseline: ConfigKey;
  dataset: string;
  source: string;
  seed: number | null;
  checks: Check[];
  side_effects: string[];
  note: string | null;
};

export type JobEvent = { seq: number; type: string; data: { row?: Record<string, string>; configuration?: string; reason?: string } };

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${path}`);
  }
  return (await response.json()) as T;
}

export async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const body = await response.json();
    throw new Error(typeof body.detail === "string" ? body.detail : `Request rejected: ${response.status}.`);
  }
  return (await response.json()) as T;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return send<T>("POST", path, body);
}

export function watchJob(jobId: string, onEvent: (event: JobEvent) => void): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  for (const type of ["queued", "started", "run_completed", "finished", "failed", "cancelled"]) {
    source.addEventListener(type, (message) => {
      const event = JSON.parse((message as MessageEvent).data) as JobEvent;
      onEvent(event);
      if (["finished", "failed", "cancelled"].includes(type)) source.close();
    });
  }
  return () => source.close();
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

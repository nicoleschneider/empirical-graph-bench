import type { JobView, LogEntry, Session } from "./api";

export const SESSION_KEY = "egb.session";

export function storedSession(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("session");
  if (fromUrl) return fromUrl;
  try {
    return window.localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

export function storeSession(id: string | null): void {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("session", id);
  else url.searchParams.delete("session");
  window.history.replaceState(null, "", url);
  try {
    if (id) window.localStorage.setItem(SESSION_KEY, id);
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    return;
  }
}

export type SessionData = {
  session: Session;
  log: LogEntry[];
  jobs: JobView[];
};

export type Iteration = {
  number: number;
  hypothesis: LogEntry | null;
  predictions: LogEntry[];
  job: JobView | null;
  outcome: LogEntry | null;
};

export function iterations(data: SessionData): Iteration[] {
  const hypotheses = data.log.filter((e) => e.kind === "hypothesis");
  return hypotheses.map((hypothesis) => ({
    number: hypothesis.iteration,
    hypothesis,
    predictions: data.log.filter((e) => e.kind === "prediction" && e.refers_to.includes(hypothesis.id)),
    job: [...data.jobs].reverse().find((j) => j.spec.hypothesis === hypothesis.id) ?? null,
    outcome: data.log.find((e) => e.kind === "outcome" && e.refers_to.includes(hypothesis.id)) ?? null,
  }));
}

export function currentIteration(data: SessionData): Iteration | null {
  const all = iterations(data);
  return all.length ? all[all.length - 1] : null;
}

export function nextIterationNumber(data: SessionData): number {
  const current = currentIteration(data);
  if (!current) return 1;
  return current.outcome ? current.number + 1 : current.number;
}

export function explorations(data: SessionData): JobView[] {
  return data.jobs.filter((j) => j.spec.kind === "exploratory");
}

export function explored(data: SessionData): boolean {
  const jobs = explorations(data);
  return jobs.length > 0 && jobs.every((j) => j.status === "finished");
}

export function decision(data: SessionData): LogEntry | null {
  const decisions = data.log.filter((e) => e.kind === "decision");
  return decisions.length ? decisions[decisions.length - 1] : null;
}

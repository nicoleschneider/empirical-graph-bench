import { Fragment, useEffect, useState } from "react";
import { get, post, type ConfigKey, type LogEntry, type Registry, type Summary } from "../api";
import { Button } from "../components/Button";
import { Checkbox } from "../components/Fields";
import { N } from "../components/Num";
import { StatusMark } from "../components/StatusMark";
import { Swatch } from "../components/Swatch";
import { Table } from "../components/Table";
import { count, gigabytes, seconds } from "../format";
import { PageHeader } from "../layout/PageHeader";
import { formatMetric } from "../components/PredictionEditor";
import { keyId, keyName, metricName, sameKey, taskOf } from "../names";
import { decision, exploration, iterations, type SessionData } from "../sessionState";
import { STATUS, STATUS_WORD } from "./Result";

type DecisionProps = {
  registry: Registry;
  data: SessionData | null;
  summaries: Summary[];
  onRefresh: () => void;
};

type Choice = { task: string; config: ConfigKey; caveats: string[] };
type LogCheck = { entries: number; problems: string[] };
type Snapshot = {
  hardware_label: string;
  specs: { cpu: string; physical_cores: number; memory_bytes: number | null; os: string; os_version: string };
  benchmark_environment: { python: string; packages: string[] };
  lock_file: { path: string; sha256: string };
  gaib: { commit: string; dirty: boolean };
  app: { commit: string; dirty: boolean };
};

function recordedTime(entry: LogEntry): string {
  return entry.recorded_at.replace("T", " ").slice(0, 19);
}

function configsFor(summaries: Summary[], task: string): ConfigKey[] {
  const seen = new Map<string, ConfigKey>();
  for (const s of summaries) if (s.task === task) seen.set(keyId(s.config), s.config);
  return [...seen.values()];
}

function wins(summaries: Summary[], task: string, config: ConfigKey): number {
  const datasets = [...new Set(summaries.filter((s) => s.task === task).map((s) => s.dataset))];
  return datasets.filter((dataset) => {
    const group = summaries.filter((s) => s.task === task && s.dataset === dataset);
    const best = group.reduce((a, b) => (a.end_to_end_s.median <= b.end_to_end_s.median ? a : b));
    return sameKey(best.config, config);
  }).length;
}

const QUALITY_TOLERANCE = 0.01;

function qualityMetric(registry: Registry, task: string): string | undefined {
  return Object.values(registry.metrics).find(
    (m) => m.tasks.includes(task) && m.better === "higher" && !m.requires_ground_truth,
  )?.id;
}

function qualityGaps(registry: Registry, summaries: Summary[], task: string, config: ConfigKey, hardwareCount: number): string[] {
  const metric = qualityMetric(registry, task);
  if (!metric) return [];
  const lines: string[] = [];
  for (const s of summaries.filter((x) => x.task === task && sameKey(x.config, config))) {
    const ours = s.effectiveness[metric]?.median;
    const best = summaries
      .filter((x) => x.task === task && x.dataset === s.dataset && x.effectiveness[metric])
      .reduce<Summary | undefined>((a, b) => (!a || b.effectiveness[metric].median > a.effectiveness[metric].median ? b : a), undefined);
    if (ours === undefined || !best || sameKey(best.config, config)) continue;
    const theirs = best.effectiveness[metric].median;
    if (theirs - ours > QUALITY_TOLERANCE) {
      lines.push(
        `${metricName(registry, metric)} ${formatMetric(registry, metric, ours)} against ${formatMetric(registry, metric, theirs)} for ${keyName(registry, best.config, hardwareCount)} on ${s.dataset}.`,
      );
    }
  }
  return lines;
}

function defaultChoice(registry: Registry, summaries: Summary[], task: string, hardwareCount: number): ConfigKey | undefined {
  const ranked = configsFor(summaries, task).sort((a, b) => wins(summaries, task, b) - wins(summaries, task, a));
  return ranked.find((c) => qualityGaps(registry, summaries, task, c, hardwareCount).length === 0) ?? ranked[0];
}

function caveatsFor(data: SessionData, summaries: Summary[], task: string, config: ConfigKey, registry: Registry, hardwareCount: number): string[] {
  const lines = qualityGaps(registry, summaries, task, config, hardwareCount);
  for (const iteration of iterations(data)) {
    const change = iteration.hypothesis?.suggestion?.change as { target?: ConfigKey } | undefined;
    const target = change?.target;
    if (!target || taskOf(registry, target.implementation) !== task) continue;
    const evaluations = (iteration.outcome?.details?.evaluations as { prediction: string; evaluation: { status: string } }[] | undefined) ?? [];
    for (const e of evaluations) lines.push(`Iteration ${iteration.number}: ${e.prediction} ${STATUS_WORD[e.evaluation.status]}.`);
    if (sameKey(target, config)) {
      for (const effect of (iteration.outcome?.details?.side_effects as string[] | undefined) ?? []) lines.push(effect);
    }
  }
  return lines;
}

export function Decision({ registry, data, summaries, onRefresh }: DecisionProps) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [caveats, setCaveats] = useState<Record<string, string>>({});
  const [check, setCheck] = useState<LogCheck | null>(null);
  const [environments, setEnvironments] = useState<Record<string, Snapshot>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const decided = data ? decision(data) : null;
  const sessionId = data?.session.meta.id;
  const logLength = data?.log.length ?? 0;

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    get<LogCheck>(`/sessions/${sessionId}/log/verify`).then((result) => active && setCheck(result));
    get<Record<string, Snapshot>>(`/sessions/${sessionId}/environment`).then((result) => active && setEnvironments(result));
    return () => {
      active = false;
    };
  }, [sessionId, logLength]);

  const header = <PageHeader screenId="decision" title="Decision" />;
  if (!data || exploration(data)?.status !== "finished") {
    return (
      <>
        {header}
        <p className="empty">Run the initial experiments.</p>
      </>
    );
  }

  const hardwareCount = new Set(summaries.map((s) => s.config.hardware)).size;
  const tasks = data.session.config.tasks.filter((task) => summaries.some((s) => s.task === task));
  const chosen = (task: string): ConfigKey | undefined => {
    const id = choices[task];
    return configsFor(summaries, task).find((c) => keyId(c) === id) ?? defaultChoice(registry, summaries, task, hardwareCount);
  };
  const caveatText = (task: string) =>
    caveats[task] ?? caveatsFor(data, summaries, task, chosen(task)!, registry, hardwareCount).join("\n");
  const recordedChoices = (decided?.details?.choices as Choice[] | undefined) ?? [];
  const slots = new Map(data.session.config.candidates.map((c) => [`${c.implementation}/${c.representation}`, c.color_slot]));

  async function record() {
    setSaving(true);
    setError(null);
    try {
      const picked: Choice[] = tasks.map((task) => ({
        task,
        config: chosen(task)!,
        caveats: caveatText(task).split("\n").map((l) => l.trim()).filter(Boolean),
      }));
      await post<LogEntry>(`/sessions/${sessionId}/log`, {
        kind: "decision",
        text: picked.map((c) => `${registry.tasks[c.task].name}: ${keyName(registry, c.config, hardwareCount)}.`).join(" "),
        iteration: Math.max(0, ...iterations(data!).map((i) => i.number)),
        refers_to: data!.log.filter((e) => e.kind === "outcome").map((e) => e.id),
        details: { choices: picked },
      });
      onRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const datasets = data.session.config.datasets;
  return (
    <>
      {header}
      {decided && (
        <section className="section">
          <div className="section-header">
            <h2>Chosen</h2>
            <span className="secondary small">
              Recorded <N>{recordedTime(decided)}</N>, entry {decided.id}
            </span>
          </div>
          <Table
            label="Chosen configurations"
            rows={recordedChoices}
            rowKey={(c) => c.task}
            columns={[
              { key: "task", label: "Task", render: (c) => registry.tasks[c.task].name },
              {
                key: "config",
                label: "Configuration",
                render: (c) => (
                  <>
                    <Swatch slot={slots.get(`${c.config.implementation}/${c.config.representation}`) ?? 1} />
                    {keyName(registry, c.config, hardwareCount)}
                  </>
                ),
              },
              {
                key: "caveats",
                label: "Caveats",
                wrap: true,
                render: (c) => (c.caveats.length ? c.caveats.join(" ") : <span className="muted">None</span>),
              },
            ]}
          />
          <div className="actions no-print" style={{ marginTop: "var(--space-3)" }}>
            <a className="button" href={`/api/sessions/${sessionId}/report.md`} download>
              Export Markdown
            </a>
            <Button onClick={() => window.print()}>Export PDF</Button>
          </div>
        </section>
      )}
      {!decided &&
        tasks.map((task) => {
          const options = configsFor(summaries, task);
          const selected = chosen(task);
          return (
            <section key={task} className="section">
              <div className="section-header">
                <h2>{registry.tasks[task].name}</h2>
              </div>
              <Table
                label={registry.tasks[task].name}
                rows={options}
                rowKey={keyId}
                columns={[
                  {
                    key: "choose",
                    label: "Configuration",
                    render: (c) => (
                      <Checkbox
                        type="radio"
                        label={
                          <>
                            <Swatch slot={slots.get(`${c.implementation}/${c.representation}`) ?? 1} />
                            {keyName(registry, c, hardwareCount)}
                          </>
                        }
                        checked={selected !== undefined && sameKey(c, selected)}
                        onChange={() => {
                          setChoices({ ...choices, [task]: keyId(c) });
                          setCaveats(Object.fromEntries(Object.entries(caveats).filter(([t]) => t !== task)));
                        }}
                      />
                    ),
                  },
                  {
                    key: "wins",
                    label: "Fastest on",
                    numeric: true,
                    render: (c) => {
                      const n = wins(summaries, task, c);
                      return `${count(n)} ${n === 1 ? "dataset" : "datasets"}`;
                    },
                  },
                  ...datasets.map((dataset) => ({
                    key: dataset,
                    label: dataset,
                    numeric: true,
                    render: (c: ConfigKey) => {
                      const s = summaries.find((x) => sameKey(x.config, c) && x.dataset === dataset);
                      return s ? seconds(s.end_to_end_s.median) : <span className="muted">None</span>;
                    },
                  })),
                ]}
              />
              <div className="field wide" style={{ marginTop: "var(--space-3)" }}>
                <label htmlFor={`${task}-caveats`}>Caveats</label>
                <textarea
                  id={`${task}-caveats`}
                  rows={3}
                  value={caveatText(task)}
                  onChange={(event) => setCaveats({ ...caveats, [task]: event.target.value })}
                />
                <span className="field-hint">One per line.</span>
              </div>
            </section>
          );
        })}
      {!decided && (
        <div className="actions section">
          <Button primary disabled={saving || tasks.length === 0} onClick={record}>
            Record decision
          </Button>
          {error && <StatusMark status="refuted" word={error} />}
        </div>
      )}
      <section className="section">
        <div className="section-header">
          <h2>Trail</h2>
          {check && (
            <span className="small">
              {check.problems.length === 0 ? (
                <StatusMark status="met" word={`${check.entries} entries, hash chain verified`} />
              ) : (
                <StatusMark status="refuted" word={`${check.problems.length} hash problems`} />
              )}
            </span>
          )}
        </div>
        <Table
          label="Trail"
          rows={data.log}
          rowKey={(e) => e.id}
          columns={[
            { key: "id", label: "Entry", render: (e) => <span className="mono">{e.id}</span> },
            { key: "time", label: "Recorded", numeric: true, render: (e) => recordedTime(e) },
            { key: "iteration", label: "Iteration", numeric: true, render: (e) => String(e.iteration) },
            { key: "kind", label: "Kind", render: (e) => e.kind.replace("_", " ") },
            { key: "text", label: "Text", wrap: true, render: (e) => e.text },
            {
              key: "status",
              label: "Status",
              render: (e) => {
                const status = e.details?.status as string | undefined;
                return status ? <StatusMark status={STATUS[status]} word={STATUS_WORD[status]} /> : null;
              },
            },
          ]}
        />
      </section>
      <section className="section">
        <div className="section-header">
          <h2>Provenance</h2>
        </div>
        <dl className="facts">
          <dt>Session</dt>
          <dd className="mono">{data.session.meta.id}</dd>
          <dt>Jobs</dt>
          <dd>
            <N>{data.jobs.length}</N> jobs on {[...new Set(data.jobs.map((j) => j.spec.hardware_label))].join(", ")}
          </dd>
          {Object.entries(environments).map(([digest, env]) => (
            <Fragment key={digest}>
              <dt>Environment</dt>
              <dd>
                <span className="mono">{digest.slice(0, 12)}</span>: {env.specs.cpu}, <N>{env.specs.physical_cores}</N> cores
                {env.specs.memory_bytes ? (
                  <>
                    , <N>{gigabytes(env.specs.memory_bytes)}</N>
                  </>
                ) : null}
                , Python <N>{env.benchmark_environment.python}</N>
              </dd>
              <dt>Lock file</dt>
              <dd>
                {env.lock_file.path.split("/").pop()} <span className="mono">{env.lock_file.sha256.slice(0, 12)}</span>
              </dd>
              <dt>gaib</dt>
              <dd>
                <span className="mono">{env.gaib.commit.slice(0, 7)}</span>
                {env.gaib.dirty ? ", modified" : ""}
              </dd>
              <dt>App</dt>
              <dd>
                <span className="mono">{env.app.commit.slice(0, 7)}</span>
                {env.app.dirty ? ", modified" : ""}
              </dd>
            </Fragment>
          ))}
          <dt>Last state</dt>
          <dd className="mono">{data.log.length ? data.log[data.log.length - 1].state_hash.slice(0, 19) : "None"}</dd>
        </dl>
      </section>
    </>
  );
}

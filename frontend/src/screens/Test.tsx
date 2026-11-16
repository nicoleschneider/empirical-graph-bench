import { useEffect, useState } from "react";
import {
  get,
  post,
  send,
  type ConfigKey,
  type JobSpec,
  type Registry,
  type SessionConfig,
  type ValidityResult,
} from "../api";
import { Button } from "../components/Button";
import { DiffView } from "../components/DiffView";
import { JobProgress } from "../components/JobProgress";
import { N } from "../components/Num";
import { StatusMark } from "../components/StatusMark";
import { Table } from "../components/Table";
import { count, quality } from "../format";
import { PageHeader } from "../layout/PageHeader";
import { runDescription } from "../names";
import { currentIteration, type SessionData } from "../sessionState";

type TestProps = {
  registry: Registry;
  data: SessionData | null;
  onRefresh: () => void;
};

function precision(registry: Registry, key: ConfigKey): string {
  const algorithm = registry.algorithms[registry.implementations[key.implementation].algorithm];
  return algorithm.precision_sensitive ? registry.representations[key.representation].precision : "Not used";
}

function settings(registry: Registry, baseline: ConfigKey, targeted: ConfigKey) {
  return [
    { key: "hardware", label: "Hardware", baseline: baseline.hardware, targeted: targeted.hardware },
    {
      key: "implementation",
      label: "Implementation",
      baseline: registry.implementations[baseline.implementation].name,
      targeted: registry.implementations[targeted.implementation].name,
    },
    {
      key: "loading",
      label: "Loading",
      baseline: registry.representations[baseline.representation].name,
      targeted: registry.representations[targeted.representation].name,
    },
    { key: "precision", label: "Precision", baseline: precision(registry, baseline), targeted: precision(registry, targeted) },
  ];
}

function candidateId(config: SessionConfig, key: ConfigKey): string | undefined {
  return config.candidates.find((c) => c.implementation === key.implementation && c.representation === key.representation)?.id;
}

function checkValue(value: number | boolean | null): string {
  if (value === null) return "None";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return Math.abs(value) < 1e-3 && value !== 0 ? value.toExponential(1) : quality(value);
}

function expected(text: string): string {
  return text.replace(/^true$/, "Yes").replace(/^>= /, "≥ ").replace(/^<= /, "≤ ").replace(/^within /, "± ");
}

const CHECK_NAMES: Record<string, string> = {
  l1: "L1 between scores",
  identical: "Identical partitions",
  nmi_between: "NMI between partitions",
  modularity_difference: "Modularity difference",
};

export function Test({ registry, data, onRefresh }: TestProps) {
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [validity, setValidity] = useState<ValidityResult[] | null>(null);
  const current = data ? currentIteration(data) : null;
  const open = current && current.hypothesis ? current : null;
  const change = open?.hypothesis?.suggestion?.change as { source: ConfigKey | null; target: ConfigKey | null } | undefined;
  const datasets = [...new Set((open?.predictions ?? []).flatMap((p) => p.prediction?.datasets ?? []))];
  const job = open?.job ?? null;
  const finished = job?.status === "finished";

  useEffect(() => {
    if (!job || !finished) return;
    let active = true;
    get<ValidityResult[]>(`/jobs/${job.spec.id}/validity`)
      .then((results) => active && setValidity(results))
      .catch((reason: Error) => active && setError(reason.message));
    return () => {
      active = false;
    };
  }, [job?.spec.id, finished]);

  const header = <PageHeader screenId="test" title="Targeted test" />;
  if (!data || !open || !change?.source || !change.target) {
    return (
      <>
        {header}
        <p className="empty">Record a hypothesis with a configuration change.</p>
      </>
    );
  }
  const { source, target } = change;
  const session = data.session;

  async function run() {
    setError(null);
    setStarting(true);
    try {
      let config = session.config;
      if (!candidateId(config, target!)) {
        config = await send<SessionConfig>("PATCH", `/sessions/${session.meta.id}/config`, {
          tasks: config.tasks,
          datasets: config.datasets,
          candidates: [
            ...config.candidates.map((c) => ({
              implementation: c.implementation,
              representation: c.representation,
              exploratory: config.exploratory.includes(c.id),
            })),
            { implementation: target!.implementation, representation: target!.representation, exploratory: false },
          ],
          metrics: config.metrics,
          repetitions: config.repetitions,
          base_seed: config.base_seed,
        });
      }
      await post<JobSpec>(`/sessions/${session.meta.id}/jobs`, {
        configurations: [candidateId(config, target!)],
        datasets,
        kind: "targeted",
        hypothesis: open!.hypothesis!.id,
        baseline: candidateId(config, source!),
      });
      onRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setStarting(false);
    }
  }

  const rows = (validity ?? []).flatMap((result) =>
    result.checks.map((check) => ({ key: `${result.dataset}-${check.name}`, dataset: result.dataset, check, source: result.source })),
  );
  const sideEffects = [...new Set((validity ?? []).flatMap((r) => r.side_effects))];
  const notes = [...new Set((validity ?? []).map((r) => r.note).filter((n): n is string => n !== null))];

  return (
    <>
      {header}
      <section className="section">
        <div className="section-header">
          <h2>Change</h2>
        </div>
        <DiffView settings={settings(registry, source, target)} />
      </section>
      <section className="section">
        <div className="section-header">
          <h2>Run</h2>
        </div>
        {!job ? (
          <>
            <p className="section-note">
              <N>{count(datasets.length * session.config.repetitions)}</N> runs on {datasets.join(", ")}, seeds from{" "}
              <N>{session.config.base_seed}</N>.
            </p>
            {error && (
              <p className="section-note">
                <StatusMark status="refuted" word={error} />
              </p>
            )}
            <Button primary disabled={starting || datasets.length === 0} onClick={run}>
              {starting ? "Starting" : "Run test"}
            </Button>
          </>
        ) : (
          <JobProgress
            jobId={job.spec.id}
            total={job.spec.items.length * (job.spec.repetitions ?? 1)}
            describe={(row) => runDescription(registry, row)}
            onEnd={onRefresh}
          />
        )}
      </section>
      {finished && (
        <section className="section">
          <div className="section-header">
            <h2>Validity</h2>
            <span className="secondary small">Run automatically against the baseline.</span>
          </div>
          {validity === null ? (
            <p className="muted">Checking</p>
          ) : (
            <>
              <Table
                label="Validity checks"
                rows={rows}
                rowKey={(row) => row.key}
                columns={[
                  { key: "dataset", label: "Dataset", render: (row) => row.dataset },
                  { key: "check", label: "Check", render: (row) => CHECK_NAMES[row.check.name] ?? row.check.name },
                  { key: "value", label: "Value", numeric: true, render: (row) => checkValue(row.check.value) },
                  { key: "expected", label: "Expected", numeric: true, render: (row) => expected(row.check.expected) },
                  {
                    key: "status",
                    label: "Status",
                    render: (row) =>
                      row.check.passed === null ? (
                        <StatusMark status="pending" word="No data" />
                      ) : row.check.passed ? (
                        <StatusMark status="met" word="Passed" />
                      ) : (
                        <StatusMark status="refuted" word="Failed" />
                      ),
                  },
                ]}
              />
              {notes.map((note) => (
                <p key={note} className="section-note">
                  {note}
                </p>
              ))}
              {sideEffects.length > 0 && (
                <div className="subsection spaced">
                  <h3>Side effects</h3>
                  <ul className="plain flagged">
                    {sideEffects.map((effect) => (
                      <li key={effect}>{effect}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
          <div className="actions">
            <Button primary onClick={() => (window.location.hash = "#/result")}>
              Compare results
            </Button>
          </div>
        </section>
      )}
    </>
  );
}

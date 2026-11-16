import { useState } from "react";
import { post, type JobSpec, type Registry, type Session, type Summary } from "../api";
import { Button } from "../components/Button";
import { JobProgress } from "../components/JobProgress";
import { N } from "../components/Num";
import { StackedBar } from "../components/StackedBar";
import { StatusMark } from "../components/StatusMark";
import { Swatch } from "../components/Swatch";
import { Table, type Column } from "../components/Table";
import { count, decimal, quality, scientific, seconds, share } from "../format";
import { PageHeader } from "../layout/PageHeader";
import { keyName, runDescription } from "../names";
import { slotFinder, useHardware } from "../hardwareContext";
import { explorations, type SessionData } from "../sessionState";
import { splitKey, type Selection } from "../selection";

type ExploreProps = {
  registry: Registry;
  selection: Selection;
  data: SessionData | null;
  summaries: Summary[];
  onSession: (id: string) => void;
  onRefresh: () => void;
};

function sessionBody(registry: Registry, selection: Selection) {
  const ordered = [...selection.configurations].sort((a, b) => selection.colors[a] - selection.colors[b]);
  return {
    name: `Session ${new Date().toISOString().slice(0, 10)}`,
    question: "Which configuration should we use?",
    config: {
      tasks: selection.tasks,
      datasets: selection.datasets,
      candidates: ordered.map((key) => ({ ...splitKey(key), exploratory: true })),
      metrics: Object.values(registry.metrics)
        .filter((m) => m.tasks.some((t) => selection.tasks.includes(t)))
        .map((m) => m.id),
      repetitions: selection.repetitions,
    },
  };
}

export function Explore({ registry, selection, data, summaries, onSession, onRefresh }: ExploreProps) {
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const jobs = data ? explorations(data) : [];
  const total = selection.configurations.length * selection.datasets.length * selection.repetitions;
  const slot = slotFinder(data?.session, useHardware());
  const hardwareCount = new Set(summaries.map((s) => s.config.hardware)).size;

  async function start() {
    setError(null);
    setStarting(true);
    try {
      const session = data?.session ?? (await post<Session>("/sessions", sessionBody(registry, selection)));
      onSession(session.meta.id);
      await post<JobSpec[]>(`/sessions/${session.meta.id}/jobs`, {
        configurations: session.config.candidates.map((c) => c.id),
        datasets: session.config.datasets,
        kind: "exploratory",
      });
      onRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setStarting(false);
    }
  }

  const phaseColumns: Column<Summary>[] = [
    {
      key: "config",
      label: "Configuration",
      render: (s) => (
        <>
          <Swatch slot={slot(s.config.hardware, s.config.implementation, s.config.representation)} />
          {keyName(registry, s.config, hardwareCount)}
        </>
      ),
    },
    { key: "dataset", label: "Dataset", render: (s) => s.dataset },
    { key: "runs", label: "Runs", numeric: true, render: (s) => count(s.runs) },
    {
      key: "etl",
      label: "ETL",
      numeric: true,
      render: (s) => <span className={s.etl_share >= 0.5 ? "dominant" : undefined}>{seconds(s.etl_s.median)}</span>,
    },
    {
      key: "processing",
      label: "Processing",
      numeric: true,
      render: (s) => (
        <span className={s.etl_share < 0.5 ? "dominant" : undefined}>{seconds(s.processing_s.median)}</span>
      ),
    },
    { key: "e2e", label: "End to end", numeric: true, render: (s) => seconds(s.end_to_end_s.median) },
    {
      key: "range",
      label: "End-to-end range",
      numeric: true,
      render: (s) => `${seconds(s.end_to_end_s.min)} to ${seconds(s.end_to_end_s.max)}`,
    },
    {
      key: "dominant",
      label: "Dominant phase",
      render: (s) => (
        <span className="dominant">
          {s.etl_share >= 0.5 ? "ETL" : "Processing"}{" "}
          <N>{share(s.etl_share >= 0.5 ? s.etl_share : 1 - s.etl_share)}</N>
        </span>
      ),
    },
  ];

  const effective = (name: string) => (s: Summary) => s.effectiveness[name]?.median;
  const effectivenessColumns: Column<Summary>[] = [
    phaseColumns[0],
    phaseColumns[1],
    { key: "modularity", label: "Modularity", numeric: true, render: (s) => maybe(effective("modularity")(s), quality) },
    { key: "nmi", label: "NMI", numeric: true, render: (s) => maybe(effective("nmi")(s), quality) },
    {
      key: "communities",
      label: "Communities",
      numeric: true,
      render: (s) => maybe(effective("predicted_communities")(s), (v) => decimal(v, 0)),
    },
    { key: "l1", label: "L1 to reference", numeric: true, render: (s) => maybe(effective("pagerank_l1")(s), scientific) },
    { key: "top", label: "Top-100 overlap", numeric: true, render: (s) => maybe(effective("pagerank_top100")(s), share) },
  ];

  const datasets = [...new Set(summaries.map((s) => s.dataset))];

  return (
    <>
      <PageHeader screenId="explore" title="Initial experiments" />
      {jobs.length === 0 ? (
        <section className="section">
          <p className="section-note">
            <N>{count(total)}</N> runs: <N>{selection.configurations.length}</N> configurations,{" "}
            <N>{selection.datasets.length}</N> datasets, <N>{selection.repetitions}</N> each.
          </p>
          {error && (
            <p className="section-note">
              <StatusMark status="refuted" word={error} />
            </p>
          )}
          <Button primary disabled={starting || total === 0} onClick={start}>
            {starting ? "Starting" : "Run experiments"}
          </Button>
        </section>
      ) : (
        <section className="section">
          <div className="section-header">
            <h2>Progress</h2>
          </div>
          {jobs.map((job) => (
            <div key={job.spec.id} className="subsection">
              <h3 className="mono">{job.spec.hardware_label}</h3>
              <JobProgress
                jobId={job.spec.id}
                total={job.spec.items.length * (job.spec.repetitions ?? 1)}
                describe={(row) => runDescription(registry, row)}
                onEnd={onRefresh}
              />
            </div>
          ))}
        </section>
      )}
      {summaries.length > 0 && (
        <>
          <section className="section">
            <div className="section-header">
              <h2>Phase times</h2>
            </div>
            {datasets.map((dataset) => (
              <div key={dataset} className="subsection">
                <h3>{dataset}</h3>
                <StackedBar
                  title={`Phase times on ${dataset}`}
                  bars={summaries
                    .filter((s) => s.dataset === dataset)
                    .map((s) => ({
                      id: `${s.config.implementation}-${s.config.representation}-${dataset}`,
                      label: keyName(registry, s.config, hardwareCount),
                      slot: slot(s.config.hardware, s.config.implementation, s.config.representation),
                      etl: s.etl_s.median,
                      processing: s.processing_s.median,
                    }))}
                />
              </div>
            ))}
            <Table label="Phase medians" columns={phaseColumns} rows={summaries} rowKey={(s) => `${s.config.implementation}-${s.config.representation}-${s.dataset}`} />
          </section>
          <section className="section">
            <div className="section-header">
              <h2>Effectiveness</h2>
            </div>
            <p className="section-note">Medians over runs.</p>
            <Table label="Effectiveness" columns={effectivenessColumns} rows={summaries} rowKey={(s) => `${s.config.implementation}-${s.config.representation}-${s.dataset}`} />
          </section>
          <div className="actions">
            <Button primary onClick={() => (window.location.hash = "#/hypothesize")}>
              Continue
            </Button>
          </div>
        </>
      )}
    </>
  );
}

function maybe(value: number | undefined, format: (v: number) => string) {
  return value === undefined ? <span className="muted">None</span> : format(value);
}

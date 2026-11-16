import { useState } from "react";
import { post, whenJobEnds, type DatasetStatus, type JobSpec, type Registry } from "../api";
import { Button } from "../components/Button";
import { Checkbox } from "../components/Fields";
import { N } from "../components/Num";
import { StatusMark } from "../components/StatusMark";
import { Table, type Column } from "../components/Table";
import { count, decimal, quality } from "../format";
import { PageHeader } from "../layout/PageHeader";
import { toggle, type Selection } from "../selection";
import type { Resource } from "../useResource";

type WorkloadProps = {
  registry: Registry;
  datasets: Resource<DatasetStatus[]>;
  selection: Selection;
  sessionId: string | null;
  onChange: (selection: Selection) => void;
  onNewSession: () => void;
};

type Row = { id: string; directed: boolean; vertices: number; edges: number; status: DatasetStatus };

function DatasetState({ status }: { status: DatasetStatus }) {
  if (status.state === "verified") return <StatusMark status="met" word="Verified" />;
  if (status.state === "mismatch") return <StatusMark status="refuted" word="Hash mismatch" />;
  if (status.state === "unverified") return <StatusMark status="pending" word="Unverified" />;
  return <StatusMark status="pending" word="Missing" />;
}

export function Workload({ registry, datasets, selection, sessionId, onChange, onNewSession }: WorkloadProps) {
  const locked = sessionId !== null;
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const statuses = datasets.data ?? [];

  async function start(key: string, label: string, path: string) {
    setError(null);
    setRunning((r) => ({ ...r, [key]: true }));
    try {
      const job = await post<JobSpec>(path);
      whenJobEnds(job.id, (status) => {
        setRunning((r) => ({ ...r, [key]: false }));
        if (status !== "finished") setError(`${label} ${status}. Try again.`);
        datasets.reload();
      });
    } catch (reason) {
      setRunning((r) => ({ ...r, [key]: false }));
      setError((reason as Error).message);
    }
  }

  const rows: Row[] = statuses.map((status) => {
    const dataset = registry.datasets[status.id];
    return { id: status.id, directed: dataset.directed, vertices: dataset.vertices, edges: dataset.edges, status };
  });
  const present = statuses.filter((s) => s.state !== "missing");
  const lacking = present.filter((s) => s.properties === null).length;

  const columns: Column<Row>[] = [
    {
      key: "dataset",
      label: "Dataset",
      render: (row) => (
        <Checkbox
          label={row.id}
          checked={selection.datasets.includes(row.id)}
          disabled={locked || row.status.state === "missing"}
          onChange={(on) => onChange({ ...selection, datasets: toggle(selection.datasets, row.id, on) })}
        />
      ),
    },
    { key: "directed", label: "Directed", render: (row) => (row.directed ? "Yes" : "No") },
    { key: "vertices", label: "Vertices", numeric: true, render: (row) => count(row.vertices) },
    { key: "edges", label: "Edges", numeric: true, render: (row) => count(row.edges) },
    {
      key: "mean",
      label: "Mean degree",
      numeric: true,
      render: (row) => (row.status.properties ? decimal(row.status.properties.degree_mean, 1) : ""),
    },
    {
      key: "max",
      label: "Max degree",
      numeric: true,
      render: (row) => (row.status.properties ? count(row.status.properties.degree_max) : ""),
    },
    {
      key: "clustering",
      label: "Clustering",
      numeric: true,
      render: (row) =>
        row.status.properties ? (
          <>
            {quality(row.status.properties.average_clustering)}
            {row.status.properties.clustering_method !== "exact" && <span className="muted"> sampled</span>}
          </>
        ) : (
          ""
        ),
    },
    { key: "truth", label: "Ground truth", render: (row) => (row.status.ground_truth ? "Yes" : "No") },
    { key: "state", label: "File", render: (row) => <DatasetState status={row.status} /> },
    {
      key: "action",
      label: "Action",
      render: (row) =>
        row.status.state === "missing" || row.status.state === "mismatch" ? (
          <Button
            disabled={running[row.id]}
            onClick={() => start(row.id, `${row.id} download`, `/datasets/${row.id}/download`)}
          >
            {running[row.id] ? "Downloading" : "Download"}
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <PageHeader screenId="workload" title="Workload" />
      {locked && (
        <section className="section">
          <div className="actions">
            <span className="secondary">
              Recorded in session <span className="mono">{sessionId}</span>.
            </span>
            <Button onClick={onNewSession}>New session</Button>
          </div>
        </section>
      )}
      <section className="section" aria-labelledby="tasks-title">
        <div className="section-header">
          <h2 id="tasks-title">Tasks</h2>
        </div>
        <div className="check-list">
          {Object.values(registry.tasks).map((task) => (
            <Checkbox
              key={task.id}
              label={task.name}
              detail={Object.values(registry.algorithms)
                .filter((a) => a.task === task.id)
                .map((a) => a.name)
                .join(", ")}
              checked={selection.tasks.includes(task.id)}
              disabled={locked}
              onChange={(on) => onChange({ ...selection, tasks: toggle(selection.tasks, task.id, on) })}
            />
          ))}
        </div>
      </section>
      <section className="section" aria-labelledby="datasets-title">
        <div className="section-header">
          <h2 id="datasets-title">Datasets</h2>
          <Button
            disabled={running.properties || present.length === 0}
            onClick={() => start("properties", "Properties", "/datasets/properties")}
          >
            {running.properties ? "Computing" : "Compute properties"}
          </Button>
        </div>
        <p className="section-note">
          <N>{present.length}</N> of <N>{statuses.length}</N> downloaded. <N>{selection.datasets.length}</N>{" "}
          selected.
          {lacking > 0 && (
            <>
              {" "}
              <N>{lacking}</N> without properties.
            </>
          )}
        </p>
        {error && (
          <p className="section-note">
            <StatusMark status="refuted" word={error} />
          </p>
        )}
        <Table label="Datasets" columns={columns} rows={rows} rowKey={(row) => row.id} />
      </section>
      <div className="actions">
        <Button primary onClick={() => (window.location.hash = "#/configurations")}>
          Continue
        </Button>
      </div>
    </>
  );
}

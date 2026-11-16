import { useState } from "react";
import { post, type ConfigurationStatus, type Hardware, type QueueView, type Registry } from "../api";
import { Button } from "../components/Button";
import { Checkbox, NumberField } from "../components/Fields";
import { N } from "../components/Num";
import { StatusMark } from "../components/StatusMark";
import { Swatch } from "../components/Swatch";
import { Table, type Column } from "../components/Table";
import { count, gigabytes } from "../format";
import { PageHeader } from "../layout/PageHeader";
import { BASE_SEED, configurationKey, toggle, withColor, type Selection } from "../selection";

type ConfigurationsProps = {
  registry: Registry;
  hardware: Hardware[];
  queues: Record<string, QueueView>;
  configurations: ConfigurationStatus[];
  selection: Selection;
  locked: boolean;
  onChange: (selection: Selection) => void;
  onProbed: () => void;
};

const CONNECTION: Record<Hardware["runner"], string> = {
  local: "This machine",
  ssh: "SSH",
  "local-shell": "Local stand-in",
};

function system(machine: Hardware): string {
  if (!machine.specs) return "";
  const { os, os_version } = machine.specs;
  return os === "Darwin" ? `macOS ${os_version}` : os_version;
}

function HardwareStatus({ machine, queue }: { machine: Hardware; queue?: QueueView }) {
  if (machine.status === "checking") return <StatusMark status="pending" word="Checking" />;
  if (machine.status === "unavailable") {
    return (
      <span>
        <StatusMark status="refuted" word="Unavailable" /> <span className="muted">{machine.reason}</span>
      </span>
    );
  }
  if (queue?.running) return <StatusMark status="pending" word="Running" />;
  if (queue && queue.queued.length > 0) return <StatusMark status="pending" word="Queued" />;
  return <StatusMark status="met" word={machine.runner === "local" ? "Ready" : "Reachable"} />;
}

function Supports({ machine }: { machine: Hardware }) {
  if (!machine.specs) return null;
  return (
    <span className="actions">
      {[
        ["cpu", "CPU"],
        ["cuda", "CUDA"],
        ["cudf", "cudf"],
      ].map(([id, name]) => (
        <StatusMark key={id} status={machine.capabilities.includes(id) ? "met" : "refuted"} word={name} />
      ))}
    </span>
  );
}

export function Configurations(props: ConfigurationsProps) {
  const { registry, hardware, queues, configurations, selection, locked, onChange, onProbed } = props;
  const [probing, setProbing] = useState<string | null>(null);
  const label = (id: string) => hardware.find((h) => h.id === id)?.label ?? id;
  const rows = configurations.filter((c) => selection.tasks.includes(c.task) && selection.hardware.includes(c.hardware));
  const chosen = rows.filter((c) =>
    selection.configurations.includes(configurationKey(c.hardware, c.implementation, c.representation)),
  );
  const runs = chosen.length * selection.datasets.length * selection.repetitions;

  async function probe(id: string) {
    setProbing(id);
    try {
      await post<Hardware>(`/hardware/${id}/probe`);
      onProbed();
    } finally {
      setProbing(null);
    }
  }

  const hardwareColumns: Column<Hardware>[] = [
    {
      key: "label",
      label: "Hardware",
      render: (machine) => (
        <Checkbox
          label={<span className="mono">{machine.label}</span>}
          checked={selection.hardware.includes(machine.id)}
          disabled={locked}
          onChange={(on) => onChange({ ...selection, hardware: toggle(selection.hardware, machine.id, on) })}
        />
      ),
    },
    {
      key: "connection",
      label: "Connection",
      render: (machine) => (
        <>
          {CONNECTION[machine.runner]}
          {machine.host && <span className="mono muted"> {machine.host}</span>}
        </>
      ),
    },
    { key: "cpu", label: "CPU", render: (machine) => machine.specs?.cpu ?? "" },
    {
      key: "cores",
      label: "Cores",
      numeric: true,
      render: (machine) => (machine.specs?.physical_cores ? count(machine.specs.physical_cores) : ""),
    },
    {
      key: "memory",
      label: "Memory",
      numeric: true,
      render: (machine) => (machine.specs?.memory_bytes ? gigabytes(machine.specs.memory_bytes) : ""),
    },
    {
      key: "gpu",
      label: "GPU",
      render: (machine) =>
        machine.specs ? (machine.specs.gpus.length ? machine.specs.gpus.map((g) => g.name).join(", ") : "None found") : "",
    },
    { key: "system", label: "System", render: system },
    { key: "supports", label: "Supports", render: (machine) => <Supports machine={machine} /> },
    {
      key: "status",
      label: "Status",
      wrap: true,
      render: (machine) => <HardwareStatus machine={machine} queue={queues[machine.id]} />,
    },
    {
      key: "check",
      label: "Action",
      render: (machine) =>
        machine.runner === "local" ? null : (
          <Button disabled={probing !== null} onClick={() => probe(machine.id)}>
            {probing === machine.id ? "Checking" : "Check again"}
          </Button>
        ),
    },
  ];

  const columns: Column<ConfigurationStatus>[] = [
    {
      key: "implementation",
      label: "Implementation",
      render: (row) => {
        const key = configurationKey(row.hardware, row.implementation, row.representation);
        const selected = selection.configurations.includes(key);
        return (
          <Checkbox
            label={
              <>
                {selected ? <Swatch slot={selection.colors[key]} /> : <span className="swatch" aria-hidden="true" />}
                {registry.implementations[row.implementation].name}
              </>
            }
            checked={selected}
            disabled={locked || !row.runnable}
            onChange={(on) =>
              onChange({
                ...selection,
                configurations: toggle(selection.configurations, key, on),
                colors: on ? withColor(selection.colors, key) : selection.colors,
              })
            }
          />
        );
      },
    },
    { key: "hardware", label: "Hardware", render: (row) => <span className="mono">{label(row.hardware)}</span> },
    { key: "algorithm", label: "Algorithm", render: (row) => registry.algorithms[row.algorithm].name },
    { key: "representation", label: "Loading", render: (row) => registry.representations[row.representation].name },
    {
      key: "precision",
      label: "Precision",
      render: (row) =>
        registry.algorithms[row.algorithm].precision_sensitive ? (
          <span className="mono">{registry.representations[row.representation].precision}</span>
        ) : (
          <span className="muted">Not used</span>
        ),
    },
    {
      key: "available",
      label: "Runs here",
      wrap: true,
      render: (row) =>
        row.runnable ? (
          <StatusMark status="met" word="Available" />
        ) : (
          <span>
            <StatusMark status="refuted" word={row.reasons[0]} />{" "}
            <span className="muted">{row.reasons.slice(1).join(" ")}</span>
          </span>
        ),
    },
  ];

  return (
    <>
      <PageHeader screenId="configurations" title="Configurations" />
      <section className="section" aria-labelledby="hardware-title">
        <div className="section-header">
          <h2 id="hardware-title">Hardware</h2>
        </div>
        <p className="section-note">
          <N>{hardware.length}</N> machines. Remote targets come from targets.yaml.
        </p>
        <Table label="Hardware" columns={hardwareColumns} rows={hardware} rowKey={(machine) => machine.id} />
      </section>
      <section className="section" aria-labelledby="configurations-title">
        <div className="section-header">
          <h2 id="configurations-title">Algorithms and loading</h2>
        </div>
        <p className="section-note">
          <N>{chosen.length}</N> of <N>{rows.length}</N> selected. <N>{rows.filter((r) => !r.runnable).length}</N>{" "}
          cannot run on their hardware.
        </p>
        {rows.length === 0 ? (
          <p className="empty">Choose a task and a machine.</p>
        ) : (
          <Table
            label="Configurations"
            columns={columns}
            rows={rows}
            rowKey={(row) => configurationKey(row.hardware, row.implementation, row.representation)}
            rowClass={(row) => (row.runnable ? undefined : "disabled")}
          />
        )}
      </section>
      <section className="section" aria-labelledby="runs-title">
        <div className="section-header">
          <h2 id="runs-title">Runs</h2>
        </div>
        <NumberField
          id="repetitions"
          label="Repetitions"
          value={selection.repetitions}
          min={1}
          max={100}
          hint={
            <>
              Seeds <N>{BASE_SEED}</N> to <N>{BASE_SEED + selection.repetitions - 1}</N>.
            </>
          }
          onChange={(repetitions) => onChange({ ...selection, repetitions })}
          disabled={locked}
        />
        <p className="section-note" style={{ marginTop: "var(--space-3)" }}>
          <N>{count(runs)}</N> runs: <N>{chosen.length}</N> configurations, <N>{selection.datasets.length}</N> datasets,{" "}
          <N>{selection.repetitions}</N> each.
        </p>
      </section>
      <div className="actions">
        <Button primary onClick={() => (window.location.hash = "#/explore")}>
          Continue
        </Button>
      </div>
    </>
  );
}

import type { ConfigurationStatus, Hardware, Registry } from "../api";
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
  configurations: ConfigurationStatus[];
  selection: Selection;
  onChange: (selection: Selection) => void;
};

const CAPABILITIES: [string, string][] = [
  ["cpu", "CPU"],
  ["cuda", "CUDA"],
  ["cudf", "cudf"],
];

function operatingSystem(machine: Hardware): string {
  const { os, os_version } = machine.specs;
  return os === "Darwin" ? `macOS ${os_version}` : os_version;
}

function cores(machine: Hardware) {
  const { physical_cores, core_groups } = machine.specs;
  return (
    <>
      <N>{physical_cores}</N> cores
      {core_groups.length > 0 && (
        <>
          {" ("}
          {core_groups.map((g, i) => (
            <span key={g.name}>
              {i > 0 && ", "}
              <N>{g.physical_cores}</N> {g.name}
            </span>
          ))}
          {")"}
        </>
      )}
    </>
  );
}

function HardwareFacts({ machine }: { machine: Hardware }) {
  const { specs } = machine;
  return (
    <dl className="facts">
      <dt>Label</dt>
      <dd className="mono">{machine.label}</dd>
      {specs.model && (
        <>
          <dt>Model</dt>
          <dd>{specs.model}</dd>
        </>
      )}
      <dt>CPU</dt>
      <dd>{specs.cpu}</dd>
      <dt>Cores</dt>
      <dd>{cores(machine)}</dd>
      <dt>Memory</dt>
      <dd>{specs.memory_bytes ? <N>{gigabytes(specs.memory_bytes)}</N> : "Unknown"}</dd>
      <dt>System</dt>
      <dd>{operatingSystem(machine)}</dd>
      <dt>GPU</dt>
      <dd>{specs.gpus.length ? specs.gpus.map((g) => g.name).join(", ") : "None found"}</dd>
      <dt>Supports</dt>
      <dd>
        <div className="check-list">
          {CAPABILITIES.map(([id, name]) =>
            machine.capabilities.includes(id) ? (
              <StatusMark key={id} status="met" word={name} />
            ) : (
              <span key={id}>
                <StatusMark status="refuted" word={name} />{" "}
                <span className="muted">{machine.unavailable[id]}</span>
              </span>
            ),
          )}
        </div>
      </dd>
    </dl>
  );
}

export function Configurations({ registry, hardware, configurations, selection, onChange }: ConfigurationsProps) {
  const machine = hardware[0];
  const rows = configurations.filter((c) => selection.tasks.includes(c.task));
  const chosen = rows.filter((c) => selection.configurations.includes(configurationKey(c.implementation, c.representation)));
  const runs = chosen.length * selection.datasets.length * selection.repetitions;

  const columns: Column<ConfigurationStatus>[] = [
    {
      key: "implementation",
      label: "Implementation",
      render: (row) => {
        const key = configurationKey(row.implementation, row.representation);
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
            disabled={!row.runnable}
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
      label: "This machine",
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
        <div className="check-list" style={{ marginBottom: "var(--space-4)" }}>
          <Checkbox type="radio" label={<span className="mono">{machine.label}</span>} detail="This machine" checked onChange={() => {}} />
          <Checkbox type="radio" label="Remote machine" detail="Not available yet." checked={false} disabled onChange={() => {}} />
        </div>
        <HardwareFacts machine={machine} />
      </section>
      <section className="section" aria-labelledby="configurations-title">
        <div className="section-header">
          <h2 id="configurations-title">Algorithms and loading</h2>
        </div>
        <p className="section-note">
          <N>{chosen.length}</N> of <N>{rows.length}</N> selected. <N>{rows.filter((r) => !r.runnable).length}</N>{" "}
          need hardware this machine lacks.
        </p>
        {rows.length === 0 ? (
          <p className="empty">Choose a task on the Workload screen.</p>
        ) : (
          <Table
            label="Configurations"
            columns={columns}
            rows={rows}
            rowKey={(row) => configurationKey(row.implementation, row.representation)}
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
        />
        <p className="section-note" style={{ marginTop: "var(--space-3)" }}>
          <N>{count(runs)}</N> runs: <N>{chosen.length}</N> configurations, <N>{selection.datasets.length}</N> datasets,{" "}
          <N>{selection.repetitions}</N> each.
        </p>
      </section>
    </>
  );
}

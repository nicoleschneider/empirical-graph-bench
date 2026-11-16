import { StatusMark } from "./StatusMark";
import { Table } from "./Table";

export type Setting = {
  key: string;
  label: string;
  baseline: string;
  targeted: string;
};

export function DiffView({ settings }: { settings: Setting[] }) {
  const changed = settings.filter((s) => s.baseline !== s.targeted);
  return (
    <div className="diff">
      <p className="section-note">
        {changed.length === 1 ? (
          <StatusMark status="met" word={`One setting changed: ${changed[0].label.toLowerCase()}.`} />
        ) : (
          <StatusMark status="refuted" word={`${changed.length} settings changed. Change one.`} />
        )}
      </p>
      <Table
        label="Changed setting"
        rows={settings}
        rowKey={(s) => s.key}
        rowClass={(s) => (s.baseline !== s.targeted ? "changed" : "same")}
        columns={[
          { key: "setting", label: "Setting", render: (s) => s.label },
          { key: "baseline", label: "Baseline", render: (s) => s.baseline },
          { key: "targeted", label: "Targeted", render: (s) => s.targeted },
          { key: "change", label: "Change", render: (s) => (s.baseline !== s.targeted ? "Changed" : "Same") },
        ]}
      />
    </div>
  );
}

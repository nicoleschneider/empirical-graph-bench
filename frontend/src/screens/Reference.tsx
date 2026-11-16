import { Button } from "../components/Button";
import { DiffView } from "../components/DiffView";
import { Checkbox, NumberField } from "../components/Fields";
import { StackedBar } from "../components/StackedBar";
import { StatusMark } from "../components/StatusMark";

export function Reference() {
  return (
    <>
      <header className="page-header">
        <h1>Components</h1>
        <p className="question">Sample values. Not measured.</p>
      </header>
      <section className="section">
        <div className="section-header">
          <h2>Status</h2>
        </div>
        <div className="actions">
          <StatusMark status="met" />
          <StatusMark status="refuted" />
          <StatusMark status="tie" />
          <StatusMark status="pending" />
        </div>
      </section>
      <section className="section">
        <div className="section-header">
          <h2>Controls</h2>
        </div>
        <div className="actions">
          <Button primary>Run</Button>
          <Button>Record</Button>
          <Button disabled>Import</Button>
          <Checkbox label="Louvain" checked onChange={() => {}} />
          <Checkbox label="cudf loading" checked={false} disabled onChange={() => {}} />
          <NumberField id="sample-repetitions" label="Repetitions" value={10} min={1} max={100} onChange={() => {}} />
        </div>
      </section>
      <section className="section">
        <div className="section-header">
          <h2>ETL and processing</h2>
        </div>
        <StackedBar
          title="Sample phase times"
          bars={[
            { id: "a", label: "Machine A, NetworkX", slot: 1, etl: 2.4, processing: 2.5 },
            { id: "b", label: "Machine B, NetworkX", slot: 2, etl: 8, processing: 8 },
            { id: "c", label: "Machine C, nx-cugraph", slot: 3, etl: 9.3, processing: 1.4 },
          ]}
        />
      </section>
      <section className="section">
        <div className="section-header">
          <h2>Changed setting</h2>
        </div>
        <DiffView
          settings={[
            { key: "hardware", label: "Hardware", baseline: "Machine C", targeted: "Machine C" },
            { key: "implementation", label: "Implementation", baseline: "nx-cugraph PageRank", targeted: "nx-cugraph PageRank" },
            { key: "representation", label: "Loading", baseline: "NetworkX loading", targeted: "cudf loading, float64" },
            { key: "precision", label: "Precision", baseline: "float64", targeted: "float64" },
          ]}
        />
      </section>
    </>
  );
}

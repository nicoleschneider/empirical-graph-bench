import type { ConfigKey, Prediction, Registry, Summary } from "../api";
import { decimal, seconds } from "../format";
import { keyId, keyName, metricName, metricUnit, sameKey, taskOf } from "../names";
import { Checkbox } from "./Fields";
import { N } from "./Num";
import { Table } from "./Table";

export type PredictionDraftState = { text: string; prediction: Prediction };

type PredictionEditorProps = {
  id: string;
  registry: Registry;
  value: PredictionDraftState;
  onChange: (value: PredictionDraftState) => void;
  configurations: ConfigKey[];
  datasets: string[];
  summaries: Summary[];
  hardwareCount: number;
  disabled: boolean;
};

export function formatMetric(registry: Registry, metric: string, value: number): string {
  const unit = metricUnit(registry, metric);
  if (unit === "s") return seconds(value);
  if (unit === "%") return `${Math.round(value * 100)} %`;
  if (metric === "predicted_communities") return decimal(value, 0);
  if (metric === "pagerank_l1") return value.toExponential(1);
  return decimal(value, 4);
}

function medianOf(summary: Summary | undefined, metric: string): number | undefined {
  if (!summary) return undefined;
  if (metric === "etl_share") return summary.etl_share;
  if (metric === "etl_s" || metric === "processing_s" || metric === "end_to_end_s") return summary[metric].median;
  return summary.effectiveness[metric]?.median;
}

export function PredictionEditor(props: PredictionEditorProps) {
  const { id, registry, value, onChange, configurations, datasets, summaries, hardwareCount, disabled } = props;
  const prediction = value.prediction;
  const task = taskOf(registry, prediction.subject.implementation);
  const metrics = [
    ...Object.values(registry.metrics).filter((m) => m.tasks.includes(task)).map((m) => m.id),
    "etl_share",
  ];
  const references = configurations.filter(
    (c) => !sameKey(c, prediction.subject) && taskOf(registry, c.implementation) === task,
  );
  const update = (changes: Partial<Prediction>) => onChange({ ...value, prediction: { ...prediction, ...changes } });
  const thresholds = prediction.datasets.map((dataset) => ({
    dataset,
    value:
      prediction.reference === null
        ? prediction.threshold ?? undefined
        : medianOf(
            summaries.find((s) => sameKey(s.config, prediction.reference!) && s.dataset === dataset),
            prediction.metric,
          ),
  }));

  return (
    <div className="prediction">
      <div className="field wide">
        <label htmlFor={`${id}-text`}>Statement</label>
        <textarea
          id={`${id}-text`}
          rows={2}
          value={value.text}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, text: event.target.value })}
        />
      </div>
      <div className="form-row">
        <div className="field">
          <label>Subject</label>
          <span className="static">{keyName(registry, prediction.subject, hardwareCount)}</span>
        </div>
        <div className="field">
          <label htmlFor={`${id}-metric`}>Metric</label>
          <select
            id={`${id}-metric`}
            value={prediction.metric}
            disabled={disabled}
            onChange={(event) => update({ metric: event.target.value })}
          >
            {metrics.map((m) => (
              <option key={m} value={m}>
                {metricName(registry, m)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-relation`}>Comparison</label>
          <select
            id={`${id}-relation`}
            value={prediction.relation}
            disabled={disabled}
            onChange={(event) => update({ relation: event.target.value as "<" | ">" })}
          >
            <option value="<">Below</option>
            <option value=">">Above</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-threshold`}>Compared with</label>
          <select
            id={`${id}-threshold`}
            value={prediction.reference ? keyId(prediction.reference) : "value"}
            disabled={disabled}
            onChange={(event) => {
              const chosen = references.find((c) => keyId(c) === event.target.value);
              update(
                chosen
                  ? { reference: chosen, threshold: null }
                  : { reference: null, threshold: thresholds[0]?.value ?? 0 },
              );
            }}
          >
            {references.map((c) => (
              <option key={keyId(c)} value={keyId(c)}>
                {keyName(registry, c, hardwareCount)}
              </option>
            ))}
            <option value="value">A fixed value</option>
          </select>
        </div>
        {prediction.reference === null && (
          <div className="field">
            <label htmlFor={`${id}-value`}>Value ({metricUnit(registry, prediction.metric) || "none"})</label>
            <input
              id={`${id}-value`}
              type="number"
              step="any"
              value={prediction.threshold ?? 0}
              disabled={disabled}
              onChange={(event) => update({ threshold: Number(event.target.value) })}
            />
          </div>
        )}
      </div>
      <div className="field">
        <label>Datasets</label>
        <div className="actions">
          {datasets.map((dataset) => (
            <Checkbox
              key={dataset}
              label={dataset}
              checked={prediction.datasets.includes(dataset)}
              disabled={disabled}
              onChange={(on) =>
                update({
                  datasets: on
                    ? [...prediction.datasets.filter((d) => d !== dataset), dataset]
                    : prediction.datasets.filter((d) => d !== dataset),
                })
              }
            />
          ))}
        </div>
      </div>
      {thresholds.length > 0 && (
        <Table
          label="Thresholds"
          rows={thresholds}
          rowKey={(row) => row.dataset}
          columns={[
            { key: "dataset", label: "Dataset", render: (row) => row.dataset },
            {
              key: "threshold",
              label: `${metricName(registry, prediction.metric)} threshold`,
              numeric: true,
              render: (row) =>
                row.value === undefined ? (
                  <span className="muted">No data</span>
                ) : (
                  <N>{formatMetric(registry, prediction.metric, row.value)}</N>
                ),
            },
          ]}
        />
      )}
    </div>
  );
}

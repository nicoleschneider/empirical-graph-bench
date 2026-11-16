import { useEffect, useState } from "react";
import { get, post, type Evaluation, type LogEntry, type Registry, type ValidityResult } from "../api";
import { Button } from "../components/Button";
import { N } from "../components/Num";
import { formatMetric } from "../components/PredictionEditor";
import { StatusMark, type Status } from "../components/StatusMark";
import { Table } from "../components/Table";
import { count } from "../format";
import { PageHeader } from "../layout/PageHeader";
import { metricName } from "../names";
import { currentIteration, type SessionData } from "../sessionState";

type ResultProps = {
  registry: Registry;
  data: SessionData | null;
  onRefresh: () => void;
};

type Evaluated = { entry: LogEntry; evaluation: Evaluation };
type Recorded = { entry: string; prediction: string; evaluation: Evaluation };

export const STATUS: Record<string, Status> = { held: "met", refuted: "refuted", narrow: "tie", pending: "pending", no_data: "pending" };
export const STATUS_WORD: Record<string, string> = { held: "Met", refuted: "Refuted", narrow: "Tie", pending: "Pending", no_data: "No data" };

export function overall(statuses: string[]): string {
  if (statuses.includes("refuted")) return "refuted";
  if (statuses.includes("pending")) return "pending";
  if (statuses.includes("narrow")) return "narrow";
  return "held";
}

function recordedTime(entry: LogEntry): string {
  return entry.recorded_at.replace("T", " ").slice(0, 19);
}

export function Result({ registry, data, onRefresh }: ResultProps) {
  const [evaluated, setEvaluated] = useState<Evaluated[] | null>(null);
  const [validity, setValidity] = useState<ValidityResult[] | null>(null);
  const [explanation, setExplanation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const current = data ? currentIteration(data) : null;
  const finished = current?.job?.status === "finished";
  const sessionId = data?.session.meta.id;
  const recorded = (current?.outcome?.details?.evaluations as Recorded[] | undefined) ?? null;

  useEffect(() => {
    if (!sessionId || !current || !finished || recorded) return;
    let active = true;
    Promise.all(
      current.predictions.map((entry) =>
        post<Evaluation>(`/sessions/${sessionId}/log/${entry.id}/evaluate`).then((evaluation) => ({ entry, evaluation })),
      ),
    )
      .then((results) => active && setEvaluated(results))
      .catch((reason: Error) => active && setError(reason.message));
    return () => {
      active = false;
    };
  }, [sessionId, current?.hypothesis?.id, finished, recorded === null]);

  useEffect(() => {
    if (!current?.job || !finished) return;
    let active = true;
    get<ValidityResult[]>(`/jobs/${current.job.spec.id}/validity`)
      .then((results) => active && setValidity(results))
      .catch((reason: Error) => active && setError(reason.message));
    return () => {
      active = false;
    };
  }, [current?.job?.spec.id, finished]);

  const header = <PageHeader screenId="result" title={`Result, iteration ${current?.number ?? 1}`} />;
  if (!data || !current || !finished) {
    return (
      <>
        {header}
        <p className="empty">Run the targeted test.</p>
      </>
    );
  }

  const shown: Recorded[] =
    recorded ?? (evaluated ?? []).map((e) => ({ entry: e.entry.id, prediction: e.entry.text, evaluation: e.evaluation }));
  const status = overall(shown.map((s) => s.evaluation.status));
  const sideEffects = [...new Set((validity ?? []).flatMap((r) => r.side_effects))];
  const failed = (validity ?? []).flatMap((r) =>
    r.checks.filter((c) => c.passed === false).map((c) => `${r.dataset}: ${c.name}`),
  );
  const waiting = validity === null && !current.outcome;

  async function finish(next: string) {
    if (current!.outcome) {
      window.location.hash = next;
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const outcome = await post<LogEntry>(`/sessions/${sessionId}/log`, {
        kind: "outcome",
        text: shown.map((s, i) => `Prediction ${i + 1} ${STATUS_WORD[s.evaluation.status].toLowerCase()}.`).join(" "),
        iteration: current!.number,
        refers_to: [current!.hypothesis!.id, ...current!.predictions.map((p) => p.id)],
        details: { status, evaluations: shown, side_effects: sideEffects, failed_checks: failed },
      });
      if (explanation.trim()) {
        await post<LogEntry>(`/sessions/${sessionId}/log`, {
          kind: "explanation",
          text: explanation.trim(),
          iteration: current!.number,
          refers_to: [outcome.id],
        });
      }
      onRefresh();
      window.location.hash = next;
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {header}
      <section className="section">
        <div className="section-header">
          <h2>
            <StatusMark status={STATUS[status]} word={`Hypothesis ${STATUS_WORD[status].toLowerCase()}`} />
          </h2>
          {current.outcome && (
            <span className="secondary small">
              Recorded <N>{recordedTime(current.outcome)}</N>, entry {current.outcome.id}
            </span>
          )}
        </div>
        <p className="locked-text">{current.hypothesis?.text}</p>
      </section>
      {evaluated === null && !recorded && <p className="muted">Evaluating</p>}
      {shown.map((item, index) => {
        const prediction = current.predictions.find((p) => p.id === item.entry)?.prediction;
        const metric = prediction?.metric ?? "end_to_end_s";
        return (
          <section key={item.entry} className="section">
            <div className="section-header">
              <h2>
                Prediction <N>{index + 1}</N>
              </h2>
              <StatusMark status={STATUS[item.evaluation.status]} word={STATUS_WORD[item.evaluation.status]} />
            </div>
            <p className="section-note">{item.prediction}</p>
            <Table
              label={`Prediction ${index + 1}`}
              rows={item.evaluation.datasets}
              rowKey={(d) => d.dataset}
              columns={[
                { key: "dataset", label: "Dataset", render: (d) => d.dataset },
                {
                  key: "subject",
                  label: `${metricName(registry, metric)}, median`,
                  numeric: true,
                  render: (d) => (d.subject_median === null ? "None" : formatMetric(registry, metric, d.subject_median)),
                },
                {
                  key: "threshold",
                  label: "Threshold",
                  numeric: true,
                  render: (d) => (d.threshold === null ? "None" : formatMetric(registry, metric, d.threshold)),
                },
                {
                  key: "ratio",
                  label: "Ratio",
                  numeric: true,
                  render: (d) => (d.ratio ? `${d.ratio.point.toFixed(2)} ×` : "None"),
                },
                {
                  key: "interval",
                  label: "95 % interval",
                  numeric: true,
                  render: (d) => (d.ratio ? `${d.ratio.low.toFixed(2)} to ${d.ratio.high.toFixed(2)} ×` : "None"),
                },
                {
                  key: "runs",
                  label: "Runs",
                  numeric: true,
                  render: (d) => `${count(d.subject_runs)}${d.reference_runs === null ? "" : ` and ${count(d.reference_runs)}`}`,
                },
                {
                  key: "status",
                  label: "Status",
                  render: (d) => <StatusMark status={STATUS[d.status]} word={STATUS_WORD[d.status]} />,
                },
              ]}
            />
          </section>
        );
      })}
      <p className="section-note">
        Ratio is predicted larger over predicted smaller. Above 1 means met.
      </p>
      <p className="section-note">
        Bootstrap 95 % interval, <N>10,000</N> resamples, seed <N>123</N>.
      </p>
      {sideEffects.length > 0 && (
        <section className="section">
          <div className="section-header">
            <h2>Side effects</h2>
          </div>
          <ul className="plain flagged">
            {sideEffects.map((effect) => (
              <li key={effect}>{effect}</li>
            ))}
          </ul>
          {failed.length > 0 && (
            <p className="section-note">
              <StatusMark status="refuted" word={`${failed.length} validity checks failed.`} />
            </p>
          )}
        </section>
      )}
      <section className="section">
        {current.outcome ? (
          (() => {
            const note = data.log.find((e) => e.kind === "explanation" && e.refers_to.includes(current.outcome!.id));
            return note ? <p className="locked-text">{note.text}</p> : null;
          })()
        ) : (
          <div className="field wide">
            <label htmlFor="explanation">Explanation</label>
            <textarea id="explanation" rows={3} value={explanation} onChange={(e) => setExplanation(e.target.value)} />
          </div>
        )}
        {error && (
          <p className="section-note">
            <StatusMark status="refuted" word={error} />
          </p>
        )}
        <div className="actions" style={{ marginTop: "var(--space-3)" }}>
          <Button disabled={saving || waiting || shown.length === 0} onClick={() => finish("#/hypothesize")}>
            Iterate
          </Button>
          <Button primary disabled={saving || waiting || shown.length === 0} onClick={() => finish("#/decision")}>
            Decide
          </Button>
          {waiting && <span className="field-hint">Checking validity</span>}
        </div>
      </section>
    </>
  );
}

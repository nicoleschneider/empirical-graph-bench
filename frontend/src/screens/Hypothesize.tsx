import { useEffect, useState } from "react";
import { post, type ConfigKey, type LogEntry, type Registry, type Suggestion, type SuggestionRecord, type Summary } from "../api";
import { Button } from "../components/Button";
import { N } from "../components/Num";
import { PredictionEditor, type PredictionDraftState } from "../components/PredictionEditor";
import { StatusMark } from "../components/StatusMark";
import { PageHeader } from "../layout/PageHeader";
import { keyName } from "../names";
import { currentIteration, explored, nextIterationNumber, type SessionData } from "../sessionState";

type HypothesizeProps = {
  registry: Registry;
  data: SessionData | null;
  summaries: Summary[];
  onRefresh: () => void;
};

type Draft = { suggestion: Suggestion; hypothesis: string; predictions: PredictionDraftState[] };

function draftOf(suggestion: Suggestion): Draft {
  return {
    suggestion,
    hypothesis: suggestion.hypothesis,
    predictions: suggestion.predictions
      .filter((p) => p.prediction !== null)
      .map((p) => ({ text: p.text, prediction: p.prediction! })),
  };
}

function changeText(registry: Registry, suggestion: Suggestion, hardwareCount: number): string {
  const { source, target, kind } = suggestion.change;
  if (!source || !target) return kind === "datasets" ? "Add datasets" : "Check outputs";
  return `${keyName(registry, source, hardwareCount)} to ${keyName(registry, target, hardwareCount)}`;
}

function recordedTime(entry: LogEntry): string {
  return entry.recorded_at.replace("T", " ").slice(0, 19);
}

export function Hypothesize({ registry, data, summaries, onRefresh }: HypothesizeProps) {
  const [suggester, setSuggester] = useState("rules");
  const [record, setRecord] = useState<SuggestionRecord | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ready = data ? explored(data) : false;
  const current = data ? currentIteration(data) : null;
  const open = current && !current.outcome ? current : null;
  const number = data ? nextIterationNumber(data) : 1;
  const configurations: ConfigKey[] = summaries.map((s) => s.config).filter(
    (c, i, all) => all.findIndex((o) => o.implementation === c.implementation && o.representation === c.representation && o.hardware === c.hardware) === i,
  );
  const hardwareCount = new Set(configurations.map((c) => c.hardware)).size;
  const sessionId = data?.session.meta.id;

  useEffect(() => {
    if (!sessionId || !ready || open) return;
    let active = true;
    setError(null);
    post<SuggestionRecord>(`/sessions/${sessionId}/suggestions`, { suggester, limit: 5 })
      .then((result) => {
        if (!active) return;
        setRecord(result);
        setDrafts(Object.fromEntries(result.response.suggestions.map((s) => [s.key, draftOf(s)])));
      })
      .catch((reason: Error) => active && setError(reason.message));
    return () => {
      active = false;
    };
  }, [sessionId, ready, open, suggester]);

  async function save(draft: Draft) {
    if (!sessionId) return;
    setSaving(true);
    setError(null);
    try {
      const edited =
        draft.hypothesis !== draft.suggestion.hypothesis ||
        draft.predictions.some((p, i) => p.text !== draft.suggestion.predictions[i]?.text);
      const hypothesis = await post<LogEntry>(`/sessions/${sessionId}/log`, {
        kind: "hypothesis",
        text: draft.hypothesis,
        iteration: number,
        source: draft.suggestion.source,
        suggestion: {
          key: draft.suggestion.key,
          rule: draft.suggestion.rule,
          change: draft.suggestion.change,
          validity_checks: draft.suggestion.validity_checks,
          caveats: draft.suggestion.caveats,
          edited,
        },
      });
      for (const prediction of draft.predictions) {
        await post<LogEntry>(`/sessions/${sessionId}/log`, {
          kind: "prediction",
          text: prediction.text,
          iteration: number,
          refers_to: [hypothesis.id],
          prediction: prediction.prediction,
          source: draft.suggestion.source,
        });
      }
      onRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const header = <PageHeader screenId="hypothesize" title={`Hypothesis, iteration ${number}`} />;
  if (!data || !ready) {
    return (
      <>
        {header}
        <p className="empty">Run the initial experiments.</p>
      </>
    );
  }

  if (open && open.hypothesis) {
    return (
      <>
        {header}
        <section className="section">
          <div className="section-header">
            <h2>Recorded</h2>
            <span className="secondary small">
              <N>{recordedTime(open.hypothesis)}</N>, entry {open.hypothesis.id}
            </span>
          </div>
          <p className="locked-text">{open.hypothesis.text}</p>
          <p className="field-hint">Recorded hypotheses are locked. Revise with a new entry.</p>
        </section>
        <section className="section">
          <div className="section-header">
            <h2>Predictions</h2>
          </div>
          {open.predictions.map((entry) => (
            <div key={entry.id} className="subsection">
              <PredictionEditor
                id={entry.id}
                registry={registry}
                value={{ text: entry.text, prediction: entry.prediction! }}
                onChange={() => {}}
                configurations={configurations}
                datasets={data.session.config.datasets}
                summaries={summaries}
                hardwareCount={hardwareCount}
                disabled
              />
            </div>
          ))}
        </section>
        <div className="actions">
          <Button primary onClick={() => (window.location.hash = "#/test")}>
            Design test
          </Button>
        </div>
      </>
    );
  }

  const suggestions = record?.response.suggestions ?? [];
  return (
    <>
      {header}
      <section className="section">
        <div className="form-row">
          <div className="field">
            <label htmlFor="suggester">Suggestions from</label>
            <select id="suggester" value={suggester} onChange={(event) => setSuggester(event.target.value)}>
              <option value="rules">Rules</option>
              <option value="agent">Agent</option>
            </select>
          </div>
        </div>
        {record?.response.status === "unavailable" && (
          <p className="section-note">
            <StatusMark status="pending" word="Agent suggestions are not available yet." />
          </p>
        )}
        {record?.response.status === "complete" && suggestions.length === 0 && (
          <p className="empty">Run more configurations to get suggestions.</p>
        )}
        {error && (
          <p className="section-note">
            <StatusMark status="refuted" word={error} />
          </p>
        )}
      </section>
      {suggestions.map((suggestion) => {
        const draft = drafts[suggestion.key];
        if (!draft) return null;
        const recordable = draft.predictions.length > 0 && draft.predictions.every((p) => p.prediction.datasets.length > 0);
        return (
          <section key={suggestion.key} className="section suggestion">
            <div className="section-header">
              <h2>
                Suggestion <N>{suggestion.rank}</N>
              </h2>
              <span className="secondary small">{changeText(registry, suggestion, hardwareCount)}</span>
            </div>
            <div className="field wide">
              <label htmlFor={`${suggestion.key}-hypothesis`}>Hypothesis</label>
              <textarea
                id={`${suggestion.key}-hypothesis`}
                rows={2}
                value={draft.hypothesis}
                onChange={(event) => setDrafts({ ...drafts, [suggestion.key]: { ...draft, hypothesis: event.target.value } })}
              />
            </div>
            {draft.predictions.map((prediction, index) => (
              <div key={index} className="subsection">
                <h3>
                  Prediction <N>{index + 1}</N>
                </h3>
                <PredictionEditor
                  id={`${suggestion.key}-${index}`}
                  registry={registry}
                  value={prediction}
                  onChange={(next) =>
                    setDrafts({
                      ...drafts,
                      [suggestion.key]: {
                        ...draft,
                        predictions: draft.predictions.map((p, i) => (i === index ? next : p)),
                      },
                    })
                  }
                  configurations={configurations}
                  datasets={data.session.config.datasets}
                  summaries={summaries}
                  hardwareCount={hardwareCount}
                  disabled={false}
                />
              </div>
            ))}
            {suggestion.predictions.filter((p) => p.prediction === null).map((p) => (
              <p key={p.text} className="section-note">
                {p.text} {p.note && <span className="muted">{p.note}</span>}
              </p>
            ))}
            {suggestion.validity_checks.length > 0 && (
              <div className="subsection">
                <h3>Validity checks</h3>
                <ul className="plain">
                  {suggestion.validity_checks.map((check) => (
                    <li key={check.text}>{check.text}</li>
                  ))}
                </ul>
              </div>
            )}
            {suggestion.caveats.length > 0 && (
              <div className="subsection">
                <h3>Caveats</h3>
                <ul className="plain">
                  {suggestion.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="actions">
              <Button primary disabled={saving || !recordable} onClick={() => save(draft)}>
                Record
              </Button>
              {!recordable && <span className="field-hint">Choose at least one dataset per prediction.</span>}
            </div>
          </section>
        );
      })}
    </>
  );
}

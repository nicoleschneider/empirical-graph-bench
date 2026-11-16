import { useEffect, useRef, useState } from "react";
import { watchJob, type JobEvent } from "../api";
import { N } from "./Num";
import { StatusMark } from "./StatusMark";

type JobProgressProps = {
  jobId: string;
  total: number;
  describe: (row: Record<string, string>) => string;
  onEnd: (status: string) => void;
};

export function JobProgress({ jobId, total, describe, onEnd }: JobProgressProps) {
  const [events, setEvents] = useState<Map<number, JobEvent>>(new Map());
  const ended = useRef(onEnd);
  ended.current = onEnd;

  useEffect(
    () =>
      watchJob(jobId, (event) => {
        setEvents((current) => new Map(current).set(event.seq, event));
        if (["finished", "failed", "cancelled"].includes(event.type)) ended.current(event.type);
      }),
    [jobId],
  );

  const ordered = [...events.values()].sort((a, b) => a.seq - b.seq);
  const runs = ordered.filter((e) => e.type === "run_completed");
  const status = [...ordered].reverse().find((e) => e.type !== "run_completed");
  const last = runs.length ? runs[runs.length - 1].data.row : undefined;
  const done = Math.min(runs.length, total);

  return (
    <div className="progress-block" aria-live="polite">
      <div className="section-header">
        <span>
          <N>{done}</N> of <N>{total}</N> runs
        </span>
        {status?.type === "failed" ? (
          <StatusMark status="refuted" word={status.data.reason ?? "Failed."} />
        ) : status?.type === "finished" ? (
          <StatusMark status="met" word="Finished" />
        ) : status?.type === "cancelled" ? (
          <StatusMark status="refuted" word="Cancelled" />
        ) : (
          <StatusMark status="pending" word={status?.type === "started" ? "Running" : "Queued"} />
        )}
      </div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <div className="progress-fill" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
      </div>
      {last && <p className="field-hint">Last: {describe(last)}</p>}
    </div>
  );
}

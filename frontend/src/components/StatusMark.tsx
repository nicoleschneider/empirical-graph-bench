export type Status = "met" | "refuted" | "tie" | "pending";

const SYMBOLS: Record<Status, string> = { met: "✓", refuted: "✗", tie: "≈", pending: "·" };
const WORDS: Record<Status, string> = { met: "Met", refuted: "Refuted", tie: "Tie", pending: "Pending" };

type StatusMarkProps = {
  status: Status;
  word?: string;
};

export function StatusMark({ status, word }: StatusMarkProps) {
  return (
    <span className={`status ${status}`}>
      <span className="symbol" aria-hidden="true">
        {SYMBOLS[status]}
      </span>
      <span>{word ?? WORDS[status]}</span>
    </span>
  );
}

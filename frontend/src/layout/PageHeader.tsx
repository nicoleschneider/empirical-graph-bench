import { DECISION, stepsOf } from "../steps";

function eyebrow(screenId: string): string | null {
  if (screenId === DECISION.id) return "After step 6";
  const steps = stepsOf(screenId);
  if (steps.length === 0) return null;
  if (steps.length === 1) return `Step ${steps[0].index}, ${steps[0].label}`;
  const labels = steps.map((s, i) => (i === 0 ? s.label : s.label.toLowerCase()));
  return `Steps ${steps.map((s) => s.index).join(" and ")}, ${labels.join(" and ")}`;
}

export function PageHeader({ screenId, title }: { screenId: string; title: string }) {
  const steps = stepsOf(screenId);
  const question = screenId === DECISION.id ? DECISION.question : steps[0]?.question;
  const label = eyebrow(screenId);
  return (
    <header className="page-header">
      {label && <p className="eyebrow">{label}</p>}
      <h1>{title}</h1>
      {question && <p className="question">{question}</p>}
    </header>
  );
}

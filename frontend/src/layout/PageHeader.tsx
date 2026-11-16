import { stepOf } from "../steps";

export function PageHeader({ screenId, title }: { screenId: string; title: string }) {
  const step = stepOf(screenId);
  return (
    <header className="page-header">
      {step && (
        <p className="eyebrow">
          Step {step.index}, {step.label}
        </p>
      )}
      <h1>{title}</h1>
      {step?.question && <p className="question">{step.question}</p>}
    </header>
  );
}

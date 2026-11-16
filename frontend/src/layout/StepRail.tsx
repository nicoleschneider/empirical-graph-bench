import { LOOP_FIRST, STEPS, type Step } from "../steps";

function StepItem({ step, screenId }: { step: Step; screenId: string }) {
  const current = step.screens.some((screen) => screen.id === screenId);
  const className = ["rail-step", current ? "current" : "", step.screens.length ? "available" : ""].join(" ");
  return (
    <li>
      <div className={className} aria-current={current ? "step" : undefined}>
        <span className="index">{step.index}</span>
        <span>{step.label}</span>
      </div>
      {step.screens.length > 0 && (
        <ul className="rail-sub">
          {step.screens.map((screen) => (
            <li key={screen.id}>
              <a href={`#/${screen.id}`} aria-current={screen.id === screenId ? "page" : undefined}>
                {screen.label}
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function StepRail({ screenId }: { screenId: string }) {
  const before = STEPS.filter((step) => step.index < LOOP_FIRST);
  const loop = STEPS.filter((step) => step.index >= LOOP_FIRST);
  return (
    <nav className="rail" aria-label="Steps">
      <p className="rail-title">Graph benchmark</p>
      <ol>
        {before.map((step) => (
          <StepItem key={step.index} step={step} screenId={screenId} />
        ))}
      </ol>
      <ol className="rail-loop">
        {loop.map((step) => (
          <StepItem key={step.index} step={step} screenId={screenId} />
        ))}
        <li className="rail-note">Repeat until decided</li>
      </ol>
    </nav>
  );
}

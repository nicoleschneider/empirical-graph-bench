import { DECISION, LOOP_FIRST, STEPS, type Step } from "../steps";

function StepItem({ step, screenId }: { step: Step; screenId: string }) {
  const current = step.screens.some((screen) => screen.id === screenId);
  const className = ["rail-step", current ? "current" : "", "available"].join(" ");
  const single = step.screens.length === 1 ? step.screens[0] : null;
  return (
    <li>
      <div className={className}>
        <span className="index">{step.index}</span>
        {single ? (
          <a href={`#/${single.id}`} aria-current={current ? "step" : undefined}>
            {step.label}
          </a>
        ) : (
          <span>{step.label}</span>
        )}
      </div>
      {step.screens.length > 1 && (
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
  const deciding = screenId === DECISION.id;
  return (
    <nav className="rail no-print" aria-label="Steps">
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
      <ol>
        <li>
          <div className={deciding ? "rail-step current available" : "rail-step available"}>
            <span className="index" />
            <a href={`#/${DECISION.id}`} aria-current={deciding ? "step" : undefined}>
              {DECISION.label}
            </a>
          </div>
        </li>
      </ol>
    </nav>
  );
}

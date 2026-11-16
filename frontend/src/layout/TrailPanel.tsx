import type { Registry } from "../api";
import type { SessionData } from "../sessionState";
import { Swimlane } from "./Swimlane";

export function TrailPanel({ registry, data }: { registry: Registry | null; data: SessionData | null }) {
  return (
    <aside className="trail" aria-label="Iteration trail">
      <div className="section-header">
        <h2>Iteration trail</h2>
      </div>
      {registry ? <Swimlane registry={registry} data={data} /> : null}
    </aside>
  );
}

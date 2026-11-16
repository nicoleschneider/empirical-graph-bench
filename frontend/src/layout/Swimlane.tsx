import type { Registry } from "../api";
import { StatusMark, type Status } from "../components/StatusMark";
import { Swatch } from "../components/Swatch";
import { taskOf } from "../names";
import { decision, exploration, iterations, type SessionData } from "../sessionState";

type Cell = { key: string; label: string; slot: number; status?: Status; chosen?: boolean };

const STATUS: Record<string, Status> = { held: "met", refuted: "refuted", narrow: "tie", pending: "pending" };

export function Swimlane({ registry, data }: { registry: Registry; data: SessionData | null }) {
  const explored = data ? exploration(data) : null;
  if (!data || !explored) {
    return <p className="empty">Run the initial experiments to start the trail.</p>;
  }
  const slots = new Map(data.session.config.candidates.map((c) => [`${c.implementation}/${c.representation}`, c.color_slot]));
  const lanes = new Map<string, { task: string; hardware: string }>();
  const cells = new Map<string, Cell[]>();
  const add = (task: string, hardware: string, column: string, cell: Cell) => {
    const lane = `${task}|${hardware}`;
    lanes.set(lane, { task, hardware });
    const key = `${lane}|${column}`;
    const list = cells.get(key) ?? [];
    if (!list.some((c) => c.key === cell.key)) cells.set(key, [...list, cell]);
  };
  const cellFor = (implementation: string, representation: string): Cell => {
    const entry = registry.implementations[implementation];
    const algorithm = registry.algorithms[entry.algorithm].name;
    const label = entry.variants.length > 1 ? `${algorithm}, ${registry.representations[representation].name}` : algorithm;
    return { key: `${implementation}/${representation}`, label, slot: slots.get(`${implementation}/${representation}`) ?? 1 };
  };

  for (const item of explored.spec.items) {
    add(taskOf(registry, item.implementation), explored.spec.hardware_label, "0", cellFor(item.implementation, item.representation));
  }
  const all = iterations(data);
  for (const iteration of all) {
    const status = (iteration.outcome?.details?.status as string | undefined) ?? "pending";
    const target = iteration.hypothesis?.suggestion?.change as { target?: { hardware: string; implementation: string; representation: string } } | undefined;
    const config = iteration.job?.spec.items[0] ?? target?.target;
    if (!config) continue;
    const hardware = iteration.job?.spec.hardware_label ?? target?.target?.hardware ?? "";
    add(taskOf(registry, config.implementation), hardware, String(iteration.number), {
      ...cellFor(config.implementation, config.representation),
      status: STATUS[status] ?? "pending",
    });
  }
  const decided = decision(data);
  const choices = ((decided?.details?.choices as { task: string; config: { hardware: string; implementation: string; representation: string } }[] | undefined) ?? []);
  for (const choice of choices) {
    add(choice.task, choice.config.hardware, "chosen", { ...cellFor(choice.config.implementation, choice.config.representation), chosen: true });
  }

  const columns = ["0", ...all.map((i) => String(i.number)), ...(choices.length ? ["chosen"] : [])];
  const header = (column: string) => (column === "0" ? "Explore" : column === "chosen" ? "Chosen" : column);

  const hardwares = [...new Set([...lanes.values()].map((l) => l.hardware))];
  return (
    <div className="swimlane" style={{ gridTemplateColumns: `72px repeat(${columns.length}, minmax(64px, 1fr))` }}>
      <div className="lane-head">Iteration</div>
      {columns.map((column) => (
        <div key={column} className={column === "0" || column === "chosen" ? "lane-head" : "lane-head n"}>
          {header(column)}
        </div>
      ))}
      {hardwares.map((hardware) => (
        <div key={hardware} className="lane-row">
          <div className="lane-group">{hardware}</div>
          {[...lanes.entries()]
            .filter(([, lane]) => lane.hardware === hardware)
            .map(([lane, { task }]) => (
              <div key={lane} className="lane-row">
                <div className="lane-label">{registry.tasks[task].name}</div>
                {columns.map((column) => (
                  <div key={column} className="lane-cell">
                    {(cells.get(`${lane}|${column}`) ?? []).map((cell) => (
                      <div key={cell.key} className={cell.chosen ? "lane-item chosen" : "lane-item"}>
                        <Swatch slot={cell.slot} />
                        <span>{cell.label}</span>
                        {cell.status && <StatusMark status={cell.status} />}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

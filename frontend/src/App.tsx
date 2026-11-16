import { useEffect, useState } from "react";
import type { ConfigurationStatus, DatasetStatus, Hardware, Registry } from "./api";
import { StatusMark } from "./components/StatusMark";
import { StepRail } from "./layout/StepRail";
import { TrailPanel } from "./layout/TrailPanel";
import { Configurations } from "./screens/Configurations";
import { Reference } from "./screens/Reference";
import { Workload } from "./screens/Workload";
import { configurationKey, type Selection } from "./selection";
import { useResource } from "./useResource";

const DEFAULT_SCREEN = "workload";
const DEFAULT_REPETITIONS = 10;

function currentScreen(): string {
  return window.location.hash.replace(/^#\//, "") || DEFAULT_SCREEN;
}

export function App() {
  const [screen, setScreen] = useState(currentScreen);
  const registry = useResource<Registry>("/registry");
  const datasets = useResource<DatasetStatus[]>("/datasets");
  const hardware = useResource<Hardware[]>("/hardware");
  const configurations = useResource<ConfigurationStatus[]>("/configurations");
  const [selection, setSelection] = useState<Selection | null>(null);

  useEffect(() => {
    const onHash = () => setScreen(currentScreen());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (selection || !registry.data || !datasets.data || !configurations.data) return;
    const runnable = configurations.data
      .filter((c) => c.runnable)
      .map((c) => configurationKey(c.implementation, c.representation));
    setSelection({
      tasks: Object.keys(registry.data.tasks),
      datasets: datasets.data.filter((d) => d.state === "verified").map((d) => d.id),
      configurations: runnable,
      colors: Object.fromEntries(runnable.map((key, i) => [key, i + 1])),
      repetitions: DEFAULT_REPETITIONS,
    });
  }, [selection, registry.data, datasets.data, configurations.data]);

  const failed = registry.error ?? datasets.error ?? hardware.error ?? configurations.error;
  let body;
  if (failed) {
    body = <StatusMark status="refuted" word="Backend not reachable. Start it with egb serve." />;
  } else if (!registry.data || !hardware.data || !configurations.data || !selection) {
    body = <p className="muted">Loading</p>;
  } else if (screen === "configurations") {
    body = (
      <Configurations
        registry={registry.data}
        hardware={hardware.data}
        configurations={configurations.data}
        selection={selection}
        onChange={setSelection}
      />
    );
  } else if (screen === "reference") {
    body = <Reference />;
  } else {
    body = <Workload registry={registry.data} datasets={datasets} selection={selection} onChange={setSelection} />;
  }

  return (
    <div className="shell">
      <StepRail screenId={screen} />
      <main className="main">{body}</main>
      <TrailPanel />
    </div>
  );
}

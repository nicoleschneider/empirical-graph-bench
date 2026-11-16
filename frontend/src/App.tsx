import { useCallback, useEffect, useState } from "react";
import type {
  ConfigurationStatus,
  DatasetStatus,
  Hardware,
  JobView,
  LogEntry,
  QueueView,
  Registry,
  Session,
  Summary,
} from "./api";
import { HardwareContext } from "./hardwareContext";
import { StatusMark } from "./components/StatusMark";
import { StepRail } from "./layout/StepRail";
import { TrailPanel } from "./layout/TrailPanel";
import { Configurations } from "./screens/Configurations";
import { Decision } from "./screens/Decision";
import { Explore } from "./screens/Explore";
import { Hypothesize } from "./screens/Hypothesize";
import { Reference } from "./screens/Reference";
import { Result } from "./screens/Result";
import { Test } from "./screens/Test";
import { Workload } from "./screens/Workload";
import { configurationKey, type Selection } from "./selection";
import { storedSession, storeSession, type SessionData } from "./sessionState";
import { useResource } from "./useResource";

const DEFAULT_SCREEN = "workload";
const DEFAULT_REPETITIONS = 10;

function currentScreen(): string {
  return window.location.hash.replace(/^#\//, "") || DEFAULT_SCREEN;
}

function selectionOf(session: Session): Selection {
  const exploratory = session.config.candidates.filter((c) => session.config.exploratory.includes(c.id));
  return {
    hardware: [...new Set(session.config.candidates.map((c) => c.hardware))],
    tasks: session.config.tasks,
    datasets: session.config.datasets,
    configurations: exploratory.map((c) => configurationKey(c.hardware, c.implementation, c.representation)),
    colors: Object.fromEntries(
      session.config.candidates.map((c) => [
        configurationKey(c.hardware, c.implementation, c.representation),
        c.color_slot,
      ]),
    ),
    repetitions: session.config.repetitions,
  };
}

export function App() {
  const [screen, setScreen] = useState(currentScreen);
  const [sessionId, setSessionId] = useState<string | null>(storedSession);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const registry = useResource<Registry>("/registry");
  const datasets = useResource<DatasetStatus[]>("/datasets");
  const hardware = useResource<Hardware[]>("/hardware");
  const configurations = useResource<ConfigurationStatus[]>("/configurations");
  const queues = useResource<Record<string, QueueView>>("/queue", version);
  const base = sessionId ? `/sessions/${sessionId}` : null;
  const session = useResource<Session>(base, version);
  const log = useResource<LogEntry[]>(base && `${base}/log`, version);
  const jobs = useResource<JobView[]>(base && `${base}/jobs`, version);
  const summary = useResource<Summary[]>(base && `${base}/summary`, version);
  const [selection, setSelection] = useState<Selection | null>(null);

  const chooseSession = useCallback((id: string | null) => {
    storeSession(id);
    setSessionId(id);
    setVersion((v) => v + 1);
  }, []);

  useEffect(() => {
    const onHash = () => setScreen(currentScreen());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (session.error) chooseSession(null);
  }, [session.error, chooseSession]);

  useEffect(() => {
    if (selection || !registry.data || !datasets.data || !configurations.data) return;
    const runnable = configurations.data
      .filter((c) => c.runnable && c.hardware === "local")
      .map((c) => configurationKey(c.hardware, c.implementation, c.representation));
    setSelection({
      hardware: ["local"],
      tasks: Object.keys(registry.data.tasks),
      datasets: datasets.data.filter((d) => d.state === "verified").map((d) => d.id),
      configurations: runnable,
      colors: Object.fromEntries(runnable.map((key, i) => [key, i + 1])),
      repetitions: DEFAULT_REPETITIONS,
    });
  }, [selection, registry.data, datasets.data, configurations.data]);

  const data: SessionData | null =
    sessionId && session.data && log.data && jobs.data && session.data.meta.id === sessionId
      ? { session: session.data, log: log.data, jobs: jobs.data }
      : null;
  const active = data ? selectionOf(data.session) : selection;
  const summaries = data ? summary.data ?? [] : [];

  const failed = registry.error ?? datasets.error ?? hardware.error ?? configurations.error;
  let body;
  if (failed) {
    body = <StatusMark status="refuted" word="Backend not reachable. Start it with egb serve." />;
  } else if (!registry.data || !hardware.data || !configurations.data || !active) {
    body = <p className="muted">Loading</p>;
  } else if (screen === "configurations") {
    body = (
      <Configurations
        registry={registry.data}
        hardware={hardware.data}
        configurations={configurations.data}
        queues={queues.data ?? {}}
        selection={active}
        locked={data !== null}
        onChange={setSelection}
        onProbed={() => {
          hardware.reload();
          configurations.reload();
        }}
      />
    );
  } else if (screen === "explore") {
    body = (
      <Explore
        registry={registry.data}
        selection={active}
        data={data}
        summaries={summaries}
        onSession={chooseSession}
        onRefresh={refresh}
      />
    );
  } else if (screen === "hypothesize") {
    body = <Hypothesize registry={registry.data} data={data} summaries={summaries} onRefresh={refresh} />;
  } else if (screen === "test") {
    body = <Test registry={registry.data} data={data} onRefresh={refresh} />;
  } else if (screen === "result") {
    body = <Result registry={registry.data} data={data} onRefresh={refresh} />;
  } else if (screen === "decision") {
    body = <Decision registry={registry.data} data={data} summaries={summaries} onRefresh={refresh} />;
  } else if (screen === "reference") {
    body = <Reference />;
  } else {
    body = (
      <Workload
        registry={registry.data}
        datasets={datasets}
        selection={active}
        sessionId={data ? data.session.meta.id : null}
        onChange={setSelection}
        onNewSession={() => chooseSession(null)}
      />
    );
  }

  return (
    <HardwareContext.Provider value={hardware.data ?? []}>
      <div className="shell">
        <StepRail screenId={screen} />
        <main className="main">{body}</main>
        <TrailPanel registry={registry.data} data={data} />
      </div>
    </HardwareContext.Provider>
  );
}

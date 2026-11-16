import json
from pathlib import Path

from egb.analysis import Analysis, ConfigKey
from egb.registry import Registry
from egb.sessions import LogCheck, LogEntry, Session, now
from egb.units import percent, seconds

STATUS_WORDS = {
    "held": "Met",
    "refuted": "Refuted",
    "narrow": "Tie",
    "pending": "Pending",
    "no_data": "No data",
}


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def config_name(key: dict | ConfigKey, registry: Registry) -> str:
    key = key if isinstance(key, ConfigKey) else ConfigKey.model_validate(key)
    implementation = registry.implementations[key.implementation].name
    representation = registry.representations[key.representation].name
    return f"{key.hardware}, {implementation}, {representation}"


def decision_section(log: list[LogEntry], registry: Registry) -> list[str]:
    decisions = [e for e in log if e.kind == "decision"]
    if not decisions:
        return ["No decision recorded."]
    latest = decisions[-1]
    lines = [
        f"Recorded {latest.recorded_at}, entry {latest.id}, state {latest.state_hash}.",
        "",
        "| Task | Configuration | Caveats |",
        "|---|---|---|",
    ]
    for choice in (latest.details or {}).get("choices", []):
        caveats = "; ".join(choice.get("caveats", [])) or "None"
        lines.append(
            f"| {registry.tasks[choice['task']].name} | {cell(config_name(choice['config'], registry))} "
            f"| {cell(caveats)} |"
        )
    if latest.text:
        lines += ["", latest.text]
    return lines


def trail_section(log: list[LogEntry]) -> list[str]:
    lines = ["| Entry | Recorded | Iteration | Kind | Text | Status |", "|---|---|---|---|---|---|"]
    for e in log:
        status = STATUS_WORDS.get((e.details or {}).get("status", ""), "")
        lines.append(
            f"| {e.id} | {e.recorded_at} | {e.iteration} | {e.kind} | {cell(e.text)} | {status} |"
        )
    return lines


def outcome_section(log: list[LogEntry], registry: Registry) -> list[str]:
    lines = []
    for e in log:
        if e.kind != "outcome" or not e.details:
            continue
        for result in e.details.get("evaluations", []):
            evaluation = result["evaluation"]
            lines += [
                f"### {result['prediction']} ({evaluation['symbol']} {STATUS_WORDS[evaluation['status']]})",
                "",
                "| Dataset | Subject median | Threshold | Ratio | 95 % interval | Status |",
                "|---|---|---|---|---|---|",
            ]
            for d in evaluation["datasets"]:
                ratio = d.get("ratio")
                interval = f"{ratio['low']:.2f} to {ratio['high']:.2f}" if ratio else ""
                point = f"{ratio['point']:.2f} ×" if ratio else ""
                median = "" if d["subject_median"] is None else f"{d['subject_median']:.4g}"
                threshold = "" if d["threshold"] is None else f"{d['threshold']:.4g}"
                lines.append(
                    f"| {d['dataset']} | {median} | {threshold} | {point} | {interval} | {STATUS_WORDS[d['status']]} |"
                )
            sample = next((d["ratio"] for d in evaluation["datasets"] if d.get("ratio")), None)
            if sample:
                lines += [
                    "",
                    "Ratio is predicted larger over predicted smaller; above 1 means met.",
                    f"Bootstrap 95 % interval, {sample['resamples']:,} resamples, seed {sample['seed']}.",
                ]
            lines.append("")
    return lines or ["No outcomes recorded."]


def results_section(analysis: Analysis, registry: Registry) -> list[str]:
    lines = [
        "| Configuration | Dataset | Runs | ETL | Processing | End to end | ETL share | Effectiveness |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in analysis.summaries:
        effectiveness = ", ".join(f"{name} {v.median:.4g}" for name, v in s.effectiveness.items())
        lines.append(
            f"| {cell(config_name(s.config, registry))} | {s.dataset} | {s.runs} | {seconds(s.etl_s.median)} "
            f"| {seconds(s.processing_s.median)} | {seconds(s.end_to_end_s.median)} "
            f"| {percent(s.etl_share)} | {effectiveness} |"
        )
    return lines


def environment_lines(digest: str, snapshot: dict) -> list[str]:
    specs = snapshot["specs"]
    bench = snapshot["benchmark_environment"]
    memory = (
        f"{round(specs['memory_bytes'] / 2**30)} GB"
        if specs.get("memory_bytes")
        else "unknown memory"
    )
    hardware = (
        f"{snapshot['hardware_label']} on {snapshot['hostname']}: {specs['cpu']}, "
        f"{specs['physical_cores']} cores, {memory}, {specs['os']} {specs['os_version']}"
    )
    lock = f"{Path(snapshot['lock_file']['path']).name} ({snapshot['lock_file']['sha256'][:12]})"
    gaib = snapshot["gaib"]["commit"] + (", modified" if snapshot["gaib"]["dirty"] else "")
    app = snapshot["app"]["commit"] + (", modified" if snapshot["app"]["dirty"] else "")
    return [
        f"Environment `{digest[:12]}`:",
        "",
        f"- Hardware: {hardware}.",
        f"- Python {bench['python']}, {len(bench['packages'])} packages, lock file {lock}.",
        f"- gaib {gaib}. App {app}.",
        "",
    ]


def provenance_section(
    folder: Path, session: Session, check: LogCheck, job_records: list[dict]
) -> list[str]:
    lines = [
        f"- Session: {session.meta.id}, created {session.meta.created_at}.",
        f"- App commit: {session.meta.app_commit}. gaib commit: {session.meta.gaib_commit}.",
        f"- Log: {check.entries} entries. "
        + (
            "Hash chain and recorded files verified."
            if not check.problems
            else "Problems: " + " ".join(check.problems)
        ),
        "",
    ]
    for path in sorted((folder / "environment").glob("*.json")):
        lines += environment_lines(path.stem, json.loads(path.read_text()))
    lines += ["| Job | Status | Recorded |", "|---|---|---|"]
    latest = {}
    for record in job_records:
        latest[record["job_id"]] = record
    for job_id, record in latest.items():
        lines.append(f"| {job_id} | {record['status']} | {record['at']} |")
    return lines


def report(
    folder: Path,
    session: Session,
    log: list[LogEntry],
    analysis: Analysis,
    registry: Registry,
    check: LogCheck,
    job_records: list[dict],
) -> str:
    sections = [
        [
            f"# {session.meta.name}",
            "",
            f"Question: {session.meta.question}",
            "",
            f"Generated {now()}.",
        ],
        ["## Decision", "", *decision_section(log, registry)],
        ["## Trail", "", *trail_section(log)],
        ["## Predictions", "", *outcome_section(log, registry)],
        [
            "## Results",
            "",
            "Medians over runs. End to end is ETL plus processing.",
            "",
            *results_section(analysis, registry),
        ],
        ["## Provenance", "", *provenance_section(folder, session, check, job_records)],
    ]
    return "\n".join("\n".join(section) + "\n" for section in sections)

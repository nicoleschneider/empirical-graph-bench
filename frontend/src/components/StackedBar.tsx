import { useState } from "react";
import { axisSeconds, seconds, share } from "../format";
import { Button } from "./Button";
import { seriesColor } from "./Swatch";
import { Table } from "./Table";

export type PhaseBar = {
  id: string;
  label: string;
  slot: number;
  etl: number;
  processing: number;
};

type StackedBarProps = {
  title: string;
  bars: PhaseBar[];
};

const WIDTH = 720;
const LABEL_WIDTH = 220;
const VALUE_WIDTH = 150;
const ROW = 20;
const GAP = 12;
const AXIS = 28;
const SEGMENT_GAP = 2;
const TICKS = 4;

function niceStep(maximum: number): number {
  const raw = maximum / TICKS;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const factor = [1, 2, 2.5, 5, 10].find((f) => f * magnitude >= raw) ?? 10;
  return factor * magnitude;
}

export function StackedBar({ title, bars }: StackedBarProps) {
  const [asTable, setAsTable] = useState(false);
  const plotWidth = WIDTH - LABEL_WIDTH - VALUE_WIDTH;
  const step = niceStep(Math.max(...bars.map((b) => b.etl + b.processing)));
  const domain = step * TICKS;
  const x = (value: number) => (value / domain) * plotWidth;
  const height = bars.length * (ROW + GAP) + AXIS;

  return (
    <figure className="chart" style={{ margin: 0 }}>
      <div className="section-header">
        <div className="chart-legend">
          <span>
            <svg width="12" height="10" aria-hidden="true">
              <rect width="12" height="10" rx="2" fill="var(--text-secondary)" />
            </svg>{" "}
            ETL
          </span>
          <span>
            <svg width="12" height="10" aria-hidden="true">
              <defs>
                <pattern id="legend-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="4" height="4" fill="var(--bg-subtle)" />
                  <line x1="0" y1="0" x2="0" y2="4" stroke="var(--text-secondary)" strokeWidth="2" />
                </pattern>
              </defs>
              <rect width="12" height="10" rx="2" fill="url(#legend-hatch)" />
            </svg>{" "}
            Processing
          </span>
        </div>
        <Button onClick={() => setAsTable(!asTable)}>{asTable ? "Show chart" : "Show table"}</Button>
      </div>
      {asTable ? (
        <Table
          label={title}
          rows={bars}
          rowKey={(bar) => bar.id}
          columns={[
            { key: "label", label: "Configuration", render: (bar) => bar.label },
            { key: "etl", label: "ETL", numeric: true, render: (bar) => seconds(bar.etl) },
            { key: "processing", label: "Processing", numeric: true, render: (bar) => seconds(bar.processing) },
            { key: "total", label: "End to end", numeric: true, render: (bar) => seconds(bar.etl + bar.processing) },
            { key: "share", label: "ETL share", numeric: true, render: (bar) => share(bar.etl / (bar.etl + bar.processing)) },
          ]}
        />
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${height}`} width={WIDTH} style={{ maxWidth: "100%", height: "auto" }} role="img" aria-label={title}>
          <defs>
            {bars.map((bar) => (
              <pattern
                key={bar.id}
                id={`hatch-${bar.id}`}
                width="4"
                height="4"
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width="4" height="4" fill="var(--bg-subtle)" />
                <line x1="0" y1="0" x2="0" y2="4" stroke={seriesColor(bar.slot)} strokeWidth="2" />
              </pattern>
            ))}
          </defs>
          {Array.from({ length: TICKS + 1 }, (_, i) => i * step).map((tick) => (
            <g key={tick}>
              <line
                x1={LABEL_WIDTH + x(tick)}
                x2={LABEL_WIDTH + x(tick)}
                y1={0}
                y2={height - AXIS + 4}
                stroke="var(--rule)"
              />
              <text x={LABEL_WIDTH + x(tick)} y={height - AXIS + 18} textAnchor="middle">
                {axisSeconds(tick)}
              </text>
            </g>
          ))}
          {bars.map((bar, i) => {
            const y = i * (ROW + GAP) + GAP / 2;
            const total = bar.etl + bar.processing;
            const etlWidth = x(bar.etl);
            return (
              <g key={bar.id}>
                <title>
                  {`${bar.label}: ETL ${seconds(bar.etl)}, processing ${seconds(bar.processing)}, end-to-end ${seconds(total)}`}
                </title>
                <text x={LABEL_WIDTH - 8} y={y + ROW / 2 + 4} textAnchor="end">
                  {bar.label}
                </text>
                <rect x={LABEL_WIDTH} y={y} width={etlWidth} height={ROW} rx="2" fill={seriesColor(bar.slot)} />
                <rect
                  x={LABEL_WIDTH + etlWidth + SEGMENT_GAP}
                  y={y}
                  width={Math.max(x(bar.processing) - SEGMENT_GAP, 0)}
                  height={ROW}
                  rx="2"
                  fill={`url(#hatch-${bar.id})`}
                />
                <text className="value" x={LABEL_WIDTH + x(total) + 8} y={y + ROW / 2 + 4}>
                  {`${seconds(total)}, ETL ${share(bar.etl / total)}`}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </figure>
  );
}

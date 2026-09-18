"use client";

import { useEffect, useRef, useState } from "react";
import type { TrendResponse } from "./intelligence";

// Created → ink columns; open at end of period → olive line (one count axis).
// Palette checked with the dataviz validator: CVD ΔE 32.6, contrast ≥ 3:1 on
// the panel surface; identity also carried by form (columns vs line) + legend.
const INK = "#242821";
const LINE = "#5c8a1c";
const GRID = "#e4e1d6";
const SURFACE = "#faf9f4";
const HEIGHT = 190;
const PAD = { top: 14, right: 40, bottom: 24, left: 30 };

function niceMax(value: number) {
  // Round up to an even multiple of half the order of magnitude, so the
  // half-way tick is a whole number (15 → 20, 25 → 30, 130 → 200). Minimum 4.
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const unit = magnitude >= 10 ? magnitude / 2 : 1;
  return Math.ceil(value / (2 * unit)) * 2 * unit;
}

function label(date: string, granularity: TrendResponse["granularity"]) {
  const value = new Date(`${date}T00:00:00Z`);
  return new Intl.DateTimeFormat(undefined, {
    day: granularity === "month" ? undefined : "2-digit",
    month: "short",
    timeZone: "UTC",
  }).format(value);
}

function columnPath(x: number, y: number, width: number, height: number) {
  const r = Math.min(4, height, width / 2);
  if (height <= 0) return "";
  return [
    `M${x},${y + height}`,
    `V${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    `H${x + width - r}`,
    `Q${x + width},${y} ${x + width},${y + r}`,
    `V${y + height}`,
    "Z",
  ].join(" ");
}

export default function TrendChart({ trend }: { trend: TrendResponse }) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(260, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const series = trend.series;
  const innerWidth = width - PAD.left - PAD.right;
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;
  const max = niceMax(Math.max(1, ...series.map((p) => Math.max(p.created, p.open_end_of_period))));
  const band = innerWidth / Math.max(series.length, 1);
  const barWidth = Math.max(2, Math.min(24, band - 2));
  const y = (value: number) => PAD.top + innerHeight - (value / max) * innerHeight;
  const cx = (index: number) => PAD.left + band * index + band / 2;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.max(1, Math.ceil(series.length / Math.max(2, Math.floor(innerWidth / 70))));
  const line = series
    .map((p, i) => `${i === 0 ? "M" : "L"}${cx(i).toFixed(1)},${y(p.open_end_of_period).toFixed(1)}`)
    .join(" ");
  const last = series.length - 1;
  const hovered = hover !== null ? series[hover] : null;

  return (
    <div className="trendChart">
      <div className="chartLegend" aria-hidden="true">
        <span><i className="legendColumn" style={{ background: INK }} />Created</span>
        <span><i className="legendLine" style={{ background: LINE }} />Open at end of {trend.granularity}</span>
      </div>

      <div className="chartCanvas" ref={wrapper} onMouseLeave={() => setHover(null)}>
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`Exceptions created per ${trend.granularity} and open exceptions over the period. ${trend.total_created} created, ${trend.total_resolved} resolved.`}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} stroke={GRID} strokeWidth={1} />
              <text x={PAD.left - 8} y={y(tick) + 3} textAnchor="end" className="chartTick">
                {tick.toLocaleString()}
              </text>
            </g>
          ))}

          {hover !== null && (
            <line x1={cx(hover)} x2={cx(hover)} y1={PAD.top} y2={PAD.top + innerHeight} stroke="#b9b6aa" strokeWidth={1} />
          )}

          {series.map((point, index) => (
            <path
              key={point.date}
              d={columnPath(cx(index) - barWidth / 2, y(point.created), barWidth, PAD.top + innerHeight - y(point.created))}
              fill={INK}
              opacity={hover === null || hover === index ? 1 : 0.45}
            />
          ))}

          <path d={line} fill="none" stroke={LINE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {last >= 0 && (
            <>
              <circle cx={cx(last)} cy={y(series[last].open_end_of_period)} r={5} fill={LINE} stroke={SURFACE} strokeWidth={2} />
              <text x={cx(last) + 9} y={y(series[last].open_end_of_period) + 3} className="chartEndLabel">
                {series[last].open_end_of_period}
              </text>
            </>
          )}
          {hover !== null && (
            <circle cx={cx(hover)} cy={y(series[hover].open_end_of_period)} r={5} fill={LINE} stroke={SURFACE} strokeWidth={2} />
          )}

          {series.map((point, index) =>
            index % labelEvery === 0 ? (
              <text key={`l-${point.date}`} x={cx(index)} y={HEIGHT - 6} textAnchor="middle" className="chartTick">
                {label(point.date, trend.granularity)}
              </text>
            ) : null
          )}

          {series.map((point, index) => (
            <rect
              key={`h-${point.date}`}
              x={PAD.left + band * index}
              y={PAD.top}
              width={band}
              height={innerHeight}
              fill="transparent"
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              tabIndex={-1}
            />
          ))}
        </svg>

        {hovered && hover !== null && (
          <div
            className="chartTooltip"
            style={{ left: Math.min(Math.max(cx(hover) - 70, 0), width - 150), top: 4 }}
          >
            <strong>{label(hovered.date, trend.granularity)}</strong>
            <span><i style={{ background: INK }} />Created <b>{hovered.created}</b></span>
            <span><i className="tooltipBlank" />Resolved <b>{hovered.resolved}</b></span>
            <span><i style={{ background: LINE }} />Open <b>{hovered.open_end_of_period}</b></span>
          </div>
        )}
      </div>

      <details className="chartTable">
        <summary>View as table</summary>
        <div className="chartTableScroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{trend.granularity === "day" ? "Date" : `${trend.granularity} of`}</th>
                <th scope="col">Created</th>
                <th scope="col">Resolved</th>
                <th scope="col">Open at end</th>
              </tr>
            </thead>
            <tbody>
              {series.map((point) => (
                <tr key={point.date}>
                  <td>{point.date}</td>
                  <td>{point.created}</td>
                  <td>{point.resolved}</td>
                  <td>{point.open_end_of_period}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

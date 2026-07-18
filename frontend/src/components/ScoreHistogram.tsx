// Score-distribution histogram for the Results threshold panel: stacked bars
// (one series per cascade stage) over score bins [0.5, 1.0], with live Match /
// Review cutoff lines that track the panel's sliders. Colors come from the
// CVD-validated --chart-N tokens; identity is never color-alone (legend +
// per-bin tooltip + screen-reader table).
import { useState } from 'react'
import type { ScoreHistogramResponse } from '../api'

const STAGE_COLOR: Record<string, string> = {
  deterministic: 'var(--chart-1)',
  probabilistic: 'var(--chart-2)',
  fuzzy: 'var(--chart-3)',
  embedding: 'var(--chart-4)',
}

const W = 640
const H = 190
const PLOT = { left: 10, right: 10, top: 26, bottom: 22 }
const X0 = 0.5 // score domain lower bound (matches the sliders)

function x(score: number): number {
  const frac = (score - X0) / (1 - X0)
  return PLOT.left + frac * (W - PLOT.left - PLOT.right)
}

export function ScoreHistogram({
  data,
  match,
  review,
}: {
  data: ScoreHistogramResponse
  match: number
  review: number
}) {
  const [hover, setHover] = useState<number | null>(null)

  const plotH = H - PLOT.top - PLOT.bottom
  const baseline = H - PLOT.bottom
  const totals = data.bins.map((b) => Object.values(b.counts).reduce((a, n) => a + n, 0))
  const maxTotal = Math.max(1, ...totals)
  const slot = (W - PLOT.left - PLOT.right) / data.bins.length
  const barW = Math.max(2, slot - 2) // 2px surface gap between adjacent bars

  const bandTotals = (lo: number, hi: number) =>
    data.stages.map((s) => ({
      stage: s,
      n: data.bins.reduce((acc, b) => acc + (b.lo >= lo && b.lo < hi ? (b.counts[s] ?? 0) : 0), 0),
    }))

  return (
    <figure className="score-histogram">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Distribution of the ${data.total} stored candidate pair scores by stage, from 0.5 to 1.0`}
        onMouseLeave={() => setHover(null)}
      >
        {/* bars: stacked per stage in cascade order, 2px gap between segments */}
        {data.bins.map((b, i) => {
          const bx = PLOT.left + i * slot + 1
          let yCursor = baseline
          const segs = data.stages
            .map((s) => ({ s, n: b.counts[s] ?? 0 }))
            .filter((seg) => seg.n > 0)
          return (
            <g key={i}>
              {segs.map((seg) => {
                const h = (seg.n / maxTotal) * plotH
                yCursor -= h
                const y = yCursor
                yCursor -= 2 // 2px surface gap between stacked segments
                return (
                  <rect
                    key={seg.s}
                    x={bx}
                    y={y}
                    width={barW}
                    height={Math.max(1, h)}
                    fill={STAGE_COLOR[seg.s] ?? 'var(--muted)'}
                  />
                )
              })}
              {/* full-height hit target, wider than the mark */}
              <rect
                x={PLOT.left + i * slot}
                y={PLOT.top}
                width={slot}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
              />
            </g>
          )
        })}

        {/* x-axis: recessive line + ticks every 0.1 */}
        <line
          x1={PLOT.left}
          y1={baseline}
          x2={W - PLOT.right}
          y2={baseline}
          stroke="var(--border)"
        />
        {[0.5, 0.6, 0.7, 0.8, 0.9, 1.0].map((t) => (
          <text
            key={t}
            x={x(t)}
            y={H - 6}
            textAnchor={t === 0.5 ? 'start' : t === 1.0 ? 'end' : 'middle'}
            className="axis-label"
          >
            {t.toFixed(1)}
          </text>
        ))}

        {/* cutoff reference lines, tracking the sliders live */}
        {[
          { v: review, label: 'Review' },
          { v: match, label: 'Match' },
        ].map(({ v, label }) => (
          <g key={label}>
            <line
              x1={x(v)}
              y1={PLOT.top - 2}
              x2={x(v)}
              y2={baseline}
              stroke="var(--text)"
              strokeDasharray="3 3"
              strokeOpacity="0.55"
            />
            <text
              x={x(v)}
              y={PLOT.top - 8}
              textAnchor={v > 0.93 ? 'end' : 'middle'}
              className="cutoff-label"
            >
              {label} ≥ {v.toFixed(2)}
            </text>
          </g>
        ))}
      </svg>

      {hover != null && totals[hover] > 0 && (
        <div
          className="chart-tooltip"
          style={{ left: `${((PLOT.left + hover * slot + slot / 2) / W) * 100}%` }}
        >
          <div className="chart-tooltip-title">
            {data.bins[hover].lo.toFixed(2)}–{data.bins[hover].hi.toFixed(2)}
          </div>
          {data.stages
            .filter((s) => (data.bins[hover].counts[s] ?? 0) > 0)
            .map((s) => (
              <div key={s}>
                <i className="swatch" style={{ background: STAGE_COLOR[s] }} /> {s}{' '}
                <strong>{data.bins[hover].counts[s]}</strong>
              </div>
            ))}
        </div>
      )}

      <figcaption className="chart-legend">
        {data.stages.map((s) => (
          <span key={s} className="legend">
            <i className="swatch" style={{ background: STAGE_COLOR[s] }} /> {s}
          </span>
        ))}
        <span className="muted small">{data.total.toLocaleString()} stored pairs</span>
      </figcaption>

      {/* table view of the same data for screen readers */}
      <table className="sr-only">
        <caption>Stored candidate pairs by stage and score band</caption>
        <thead>
          <tr>
            <th>Stage</th>
            <th>Below review (&lt; {review.toFixed(2)})</th>
            <th>Review band</th>
            <th>Match band (≥ {match.toFixed(2)})</th>
          </tr>
        </thead>
        <tbody>
          {data.stages.map((s) => {
            const below = bandTotals(0, review).find((t) => t.stage === s)?.n ?? 0
            const rev = bandTotals(review, match).find((t) => t.stage === s)?.n ?? 0
            const hi = bandTotals(match, 1.01).find((t) => t.stage === s)?.n ?? 0
            return (
              <tr key={s}>
                <td>{s}</td>
                <td>{below}</td>
                <td>{rev}</td>
                <td>{hi}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </figure>
  )
}

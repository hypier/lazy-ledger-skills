import React, { useState } from 'react';
import { dayColumns, ringSegments } from './chart-geometry.mjs';

export function Icon({ name, className = '' }) {
  const paths = {
    income: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
    expense: 'M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5',
    refund: 'M8 5 3 10l5 5M3 10h11a6 6 0 0 1 0 12',
    net: 'M3 17 9 11l4 4 8-10m-6 0h6v6',
    chart: 'M4 20V10m8 10V4m8 16v-7',
    budget: 'M3 7h18v14H3zM3 7V4h14v3m-2 6h6v4h-6z',
    empty: 'M5 3h14v18l-4-2-3 2-3-2-4 2zM9 8h6m-6 4h6',
  };
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name] || paths.chart} /></svg>;
}

export function RingChart({ rows, currency }) {
  const [selected, setSelected] = useState(null);
  const [hovered, setHovered] = useState(null);
  const segments = ringSegments(rows);
  const active = hovered ?? selected;
  const item = active == null ? null : segments[active];
  return <div className="ring-layout">
    <div className="ring-graphic">
      <svg viewBox="0 0 220 220" className="ring-chart" role="img" aria-label="已记录支出的分类占比；金额及分类可在下方列表查看">
        <circle cx="110" cy="110" r="80" fill="none" stroke="#edf0f6" strokeWidth="24" />
        {segments.map((row, i) => row.length > 0 && <circle key={row.label} cx="110" cy="110" r="80" pathLength="100" fill="none"
          stroke={row.color} strokeWidth={active === i ? 30 : 24} strokeDasharray={`${row.length} ${100 - row.length}`}
          strokeDashoffset={-row.offset} transform="rotate(-90 110 110)" className="ring-segment"
          opacity={active != null && active !== i ? .3 : 1}
          onPointerEnter={() => setHovered(i)} onPointerLeave={() => setHovered(null)}
          onClick={() => { setSelected(selected === i ? null : i); setHovered(null); }}>
          <title>{row.label}：{row.amount} {currency}</title>
        </circle>)}
      </svg>
      <div className="ring-center" aria-hidden="true"><span>{item ? item.label : '支出分类'}</span><strong>{item ? item.amount : rows.length}</strong><small>{item ? currency : '个已记录分类'}</small></div>
    </div>
    <div className="chart-legend" aria-label="选择支出分类">
      {segments.map((row, i) => <button key={row.label} type="button" aria-pressed={selected === i}
        onPointerEnter={() => setHovered(i)} onPointerLeave={() => setHovered(null)}
        onFocus={() => setHovered(i)} onBlur={() => setHovered(null)}
        onClick={() => { setSelected(selected === i ? null : i); setHovered(null); }}>
        <span className="legend-dot" style={{ background: row.color }} /><span>{row.label}</span><strong>{row.amount}</strong>
      </button>)}
      <p className="muted">悬停查看 · 点击固定分类，再次点击取消</p>
    </div>
  </div>;
}

export function DayChart({ rows, currency, period }) {
  const [selected, setSelected] = useState(0);
  const [hovered, setHovered] = useState(null);
  const points = dayColumns(rows, period.start, period.end);
  const active = hovered ?? selected;
  const item = points[active];
  const selectedLabel = item ? `${item.label} · ${item.amount} ${currency}` : '暂无数据';
  return <div className="day-chart">
    <div className="chart-readout"><span>已记录支出</span><strong>{selectedLabel}</strong></div>
    <svg viewBox="0 0 420 220" role="img" aria-label={`每日已记录支出，横轴日期，纵轴相对金额；${selectedLabel}`}>
      <text x="44" y="17" className="axis-label">金额（{currency}）</text>
      {[36, 107, 178].map(y => <line key={y} x1="44" y1={y} x2="394" y2={y} className="chart-gridline" />)}
      <text x="32" y="182" textAnchor="end" className="axis-label">0</text>
      <text x="394" y="17" textAnchor="end" className="axis-label">最高 {rows.reduce((top, row) => Number(row.amount) > Number(top) ? row.amount : top, '0.00')}</text>
      {points.map((point, i) => <g key={point.label} className="day-column"
        onPointerEnter={() => setHovered(i)} onPointerLeave={() => setHovered(null)} onClick={() => setSelected(i)}>
        <rect x={point.x - Math.max(point.width, 8) / 2} y="30" width={Math.max(point.width, 8)} height="152" fill="transparent" />
        <rect x={point.x - point.width / 2} y={178 - point.height} width={point.width} height={point.height} rx="3"
          fill={active === i ? '#345ec0' : '#9cb3df'} />
        <title>{point.label}：{point.amount} {currency}</title>
      </g>)}
      <text x="44" y="205" className="axis-label">{period.start}</text>
      <text x="394" y="205" textAnchor="end" className="axis-label">{period.end}</text>
    </svg>
    <label className="day-picker">查看日期<select value={selected} onChange={event => { setSelected(Number(event.target.value)); setHovered(null); }}>
      {points.map((point, i) => <option key={point.label} value={i}>{point.label} · {point.amount} {currency}</option>)}
    </select></label>
    <p className="muted">空白日期表示没有记录，不代表零支出。</p>
  </div>;
}

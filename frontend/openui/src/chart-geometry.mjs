// Geometry only: all displayed amounts remain the core's original strings.
export const palette = ['#b65a3d', '#527b88', '#8b8246', '#53785a', '#b88736', '#547d78'];

export function ringSegments(rows) {
  const weights = rows.map(row => Number(row.amount));
  if (weights.some(value => !Number.isFinite(value) || value < 0)) throw new Error('Invalid chart amount');
  const sum = weights.reduce((a, b) => a + b, 0);
  let offset = 0;
  return rows.map((row, index) => {
    const length = sum ? weights[index] / sum * 100 : 0;
    const segment = { ...row, length, offset, color: palette[index % palette.length] };
    offset += length;
    return segment;
  });
}

export function dayColumns(rows, start, end) {
  const begin = Date.parse(start), finish = Date.parse(end);
  const span = (finish - begin) / 86400000 + 1;
  if (!Number.isFinite(span) || span < 1) throw new Error('Invalid chart period');
  const amounts = rows.map(row => Number(row.amount));
  if (amounts.some(value => !Number.isFinite(value) || value < 0)) throw new Error('Invalid chart amount');
  const max = Math.max(1, ...amounts);
  return rows.map((row, index) => {
    const day = (Date.parse(row.label) - begin) / 86400000;
    if (!Number.isFinite(day) || day < 0 || day >= span) throw new Error('Date outside chart period');
    return { ...row, x: 44 + (day + .5) / span * 350,
      width: Math.min(24, 350 / span * .65), height: amounts[index] / max * 142 };
  });
}

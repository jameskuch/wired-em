// Inline SVG charts. Small enough not to justify a plotting dependency, and
// they inherit the page's light/dark tokens rather than baking in colors.

const NS = 'http://www.w3.org/2000/svg';

const el = (name, attrs = {}, text) => {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
};

/**
 * Stratification profile: annotation counts against IPL depth.
 * 0 is the INL/IPL border and 1 the IPL/GCL border, so the shaded margins
 * outside [0, 1] are annotations beyond the IPL, exactly as the MATLAB
 * iplDepth plot shaded them.
 */
export function stratificationChart(container, series, { width = 720, height = 340 } = {}) {
  container.replaceChildren();
  const usable = series.filter((s) => s.centers.length);
  if (!usable.length) {
    container.append(Object.assign(document.createElement('p'), {
      className: 'empty',
      textContent: 'No annotations fall within the fitted IPL boundary surfaces.',
    }));
    return;
  }

  const m = { top: 16, right: 16, bottom: 42, left: 54 };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;

  const xs = usable.flatMap((s) => s.centers);
  const ys = usable.flatMap((s) => s.counts);
  const x0 = Math.min(-0.25, Math.min(...xs));
  const x1 = Math.max(1.25, Math.max(...xs));
  const y1 = Math.max(1, Math.max(...ys));

  const sx = (v) => m.left + ((v - x0) / (x1 - x0)) * w;
  const sy = (v) => m.top + h - (v / y1) * h;

  const svg = el('svg', {
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': 'IPL stratification profile',
  });

  // Shaded regions beyond the IPL.
  svg.append(el('rect', {
    x: sx(x0), y: m.top, width: sx(0) - sx(x0), height: h,
    fill: 'currentColor', opacity: 0.06,
  }));
  svg.append(el('rect', {
    x: sx(1), y: m.top, width: sx(x1) - sx(1), height: h,
    fill: 'currentColor', opacity: 0.06,
  }));

  // Axes.
  svg.append(el('line', {
    x1: m.left, y1: m.top + h, x2: m.left + w, y2: m.top + h,
    stroke: 'currentColor', opacity: 0.35,
  }));
  svg.append(el('line', {
    x1: m.left, y1: m.top, x2: m.left, y2: m.top + h,
    stroke: 'currentColor', opacity: 0.35,
  }));

  for (const [value, label] of [[0, 'INL'], [0.25, '25'], [0.5, '50'], [0.75, '75'], [1, 'GCL']]) {
    svg.append(el('line', {
      x1: sx(value), y1: m.top, x2: sx(value), y2: m.top + h,
      stroke: 'currentColor', opacity: 0.12, 'stroke-dasharray': '3 3',
    }));
    svg.append(el('text', {
      x: sx(value), y: m.top + h + 18, 'text-anchor': 'middle',
      'font-size': 11, fill: 'currentColor', opacity: 0.75,
    }, label));
  }
  for (const value of [0, y1 / 2, y1]) {
    svg.append(el('text', {
      x: m.left - 8, y: sy(value) + 4, 'text-anchor': 'end',
      'font-size': 11, fill: 'currentColor', opacity: 0.75,
    }, Math.round(value)));
  }
  svg.append(el('text', {
    x: m.left + w / 2, y: height - 6, 'text-anchor': 'middle',
    'font-size': 12, fill: 'currentColor', opacity: 0.8,
  }, 'IPL depth (%)'));

  for (const s of usable) {
    const points = s.centers.map((c, i) => `${sx(c)},${sy(s.counts[i])}`).join(' ');
    svg.append(el('polyline', {
      points, fill: 'none', stroke: s.color, 'stroke-width': 1.8,
      'stroke-linejoin': 'round',
    }));
    for (let i = 0; i < s.centers.length; i += 1) {
      svg.append(el('circle', {
        cx: sx(s.centers[i]), cy: sy(s.counts[i]), r: 2.6, fill: s.color,
      }));
    }
    if (Number.isFinite(s.median)) {
      svg.append(el('path', {
        d: markerPath(sx(s.median), sy(y1 * 0.06)),
        fill: s.color, opacity: 0.85,
      }));
    }
  }

  const legend = el('g');
  usable.forEach((s, i) => {
    const y = m.top + 14 + i * 16;
    legend.append(el('rect', { x: m.left + w - 130, y: y - 8, width: 10, height: 10, fill: s.color }));
    legend.append(el('text', {
      x: m.left + w - 114, y, 'font-size': 11, fill: 'currentColor',
    }, s.label));
  });
  svg.append(legend);
  container.append(svg);
}

const markerPath = (x, y) => `M ${x} ${y} l -5 -9 l 10 0 z`;

/** Round tick values covering [lo, hi]: 1, 2 or 5 times a power of ten, about `count` of them. */
export function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) return [lo];
  const rough = (hi - lo) / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((candidate) => candidate >= rough) ?? 10 * power;
  const ticks = [];
  // Multiples of the step, not a running sum of it: 0.2 added three times is 0.6000000000000001.
  for (let k = Math.ceil(lo / step); k * step <= hi + step * 1e-9; k += 1) {
    ticks.push(Number((k * step).toPrecision(12)));
  }
  return ticks;
}

/**
 * Where a bar chart puts its bars: each at the x value of its row (the center
 * of its bin, `binWidth` wide), not at the row's place in the list.  A profile
 * binned by distance has empty bins, which are dropped from the rows; placed by
 * index, every bar after one slid a bin to the left and the axis said nothing
 * (c1178 and c10912 each have one).  `xKey` absent places bars by index, as
 * before.  The axis starts at 0 or at the first bin's edge, whichever is lower.
 *
 * Pure, so it is tested without a DOM: returns `{ bars, ticks, y1, plot }`
 * with every bar's `x`, `width`, `height` and `y` in the chart's pixels.
 */
export function barLayout(rows, {
  xKey, yKey, binWidth, width = 420, height = 220,
  margin = { top: 10, right: 10, bottom: 34, left: 44 },
} = {}) {
  const data = rows
    .map((row, index) => ({ row, at: xKey ? Number(row[xKey]) : index, y: row[yKey] }))
    .filter((d) => Number.isFinite(d.y) && Number.isFinite(d.at));
  const w = width - margin.left - margin.right;
  const h = height - margin.top - margin.bottom;
  const plot = { left: margin.left, top: margin.top, width: w, height: h };
  if (!data.length) return { bars: [], ticks: [], y1: 0, plot };

  const xs = data.map((d) => d.at).sort((a, b) => a - b);
  let bin = Number(binWidth);
  if (!(bin > 0)) {
    // Unstated: the closest two rows are neighbors.
    bin = xs.slice(1).map((x, i) => x - xs[i]).filter((d) => d > 0).reduce((m, d) => Math.min(m, d), Infinity);
    if (!Number.isFinite(bin)) bin = 1;
  }
  const x0 = Math.min(0, xs[0] - bin / 2);
  const x1 = xs[xs.length - 1] + bin / 2;
  const y1 = Math.max(...data.map((d) => d.y), Number.MIN_VALUE);
  const sx = (v) => margin.left + ((v - x0) / (x1 - x0)) * w;
  const bars = data.map((d) => {
    const left = sx(d.at - bin / 2);
    const barHeight = (Math.max(d.y, 0) / y1) * h;
    return {
      at: d.at, value: d.y, row: d.row,
      x: left + 1, width: Math.max(sx(d.at + bin / 2) - left - 2, 1),
      height: barHeight, y: margin.top + h - barHeight,
    };
  });
  const ticks = niceTicks(x0, x1).map((value) => ({ value, x: sx(value) }));
  return { bars, ticks, y1, plot };
}

/** Bar chart used for dendrite-diameter profiles: bars at their x values, with an x axis. */
export function barChart(container, rows, {
  xKey, yKey, binWidth, xLabel = '', yLabel = '', color = '#2f6fed', width = 420, height = 220,
} = {}) {
  container.replaceChildren();
  const layout = barLayout(rows, { xKey, yKey, binWidth, width, height });
  if (!layout.bars.length) {
    container.append(Object.assign(document.createElement('p'), {
      className: 'empty', textContent: 'No data',
    }));
    return;
  }
  const { plot, bars, ticks, y1 } = layout;
  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': yLabel });
  svg.append(el('line', {
    x1: plot.left, y1: plot.top + plot.height, x2: plot.left + plot.width, y2: plot.top + plot.height,
    stroke: 'currentColor', opacity: 0.35,
  }));
  for (const bar of bars) {
    const rect = el('rect', {
      x: bar.x, y: bar.y, width: bar.width, height: bar.height, fill: color, opacity: 0.85,
    });
    // The bar's own numbers on hover.
    rect.append(el('title', {}, `${Number(bar.at.toPrecision(4))}: ${Number(bar.value.toPrecision(4))}`));
    svg.append(rect);
  }
  for (const tick of ticks) {
    svg.append(el('line', {
      x1: tick.x, y1: plot.top + plot.height, x2: tick.x, y2: plot.top + plot.height + 4,
      stroke: 'currentColor', opacity: 0.35,
    }));
    svg.append(el('text', {
      x: tick.x, y: plot.top + plot.height + 15, 'text-anchor': 'middle', 'font-size': 10,
      fill: 'currentColor', opacity: 0.75,
    }, Number(tick.value.toPrecision(6))));
  }
  svg.append(el('text', {
    x: plot.left - 8, y: plot.top + 10, 'text-anchor': 'end', 'font-size': 11, fill: 'currentColor',
  }, y1.toFixed(2)));
  if (yLabel) {
    // The value axis says what it measures and in what unit, turned to read up the left edge.
    const x = 11;
    const y = plot.top + plot.height / 2;
    svg.append(el('text', {
      x, y, transform: `rotate(-90 ${x} ${y})`, 'text-anchor': 'middle', 'font-size': 11,
      fill: 'currentColor', opacity: 0.8,
    }, yLabel));
  }
  svg.append(el('text', {
    x: plot.left + plot.width / 2, y: height - 6, 'text-anchor': 'middle', 'font-size': 11,
    fill: 'currentColor', opacity: 0.8,
  }, xLabel));
  container.append(svg);
}

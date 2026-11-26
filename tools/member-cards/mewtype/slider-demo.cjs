module.exports = function installDemo(channels) {
  const svg = document.querySelector('svg.fader-bank');
  const format = new Intl.NumberFormat('en-US');
  const registrations = Symbol.for('mewtype.axisReadoutDemo');
  if (svg[registrations]) return;

  const cap = channels[0].levels.length;
  const min = Number(svg.dataset.min), max = Number(svg.dataset.max);
  const xMin = Number(svg.dataset.xmin), xMax = Number(svg.dataset.xmax);
  const xPerUnit = (xMax - xMin) / (max - min);
  const axisX = value => Number((xMin + (value - min) * xPerUnit).toFixed(3));
  const rows = channels.map(channel => {
    const strip = svg.querySelector('.strip[data-key="' + channel.key + '"]');
    const slider = strip.querySelector('.slider');
    const ticks = channel.levels.map((value, i) => ({x: axisX(value), level: i + 1}));
    if (ticks.length !== cap || !Number.isFinite(xPerUnit) || xPerUnit <= 0) {
      throw new Error('Invalid level scale for ' + channel.key);
    }
    for (const mark of strip.querySelectorAll('.graduation')) {
      const index = Number(mark.dataset.level) - 1;
      if (Number(mark.dataset.value) !== channel.levels[index] ||
          Math.abs(Number(mark.dataset.x) - ticks[index].x) > .01) {
        throw new Error('Printed tick disagrees with the level table for ' + channel.key);
      }
    }
    strip.setAttribute('role', 'slider');
    strip.setAttribute('tabindex', '0');
    strip.setAttribute('aria-label', channel.label + '等级');
    strip.setAttribute('aria-orientation', 'horizontal');
    strip.setAttribute('aria-valuemin', '1');
    strip.setAttribute('aria-valuemax', String(cap));
    strip.style.cursor = 'ew-resize';
    const current = strip.querySelector('.read-current');
    const upper = ['.read-initial', '.read-full', '.read-mid-high']
      .map(selector => strip.querySelector(selector)).filter(Boolean);
    const lower = ['.read-max', '.read-mid-low']
      .map(selector => strip.querySelector(selector)).filter(Boolean);
    // Keep hidden readouts measurable, including those hidden in the static drawing.
    for (const readout of [current, ...lower, ...upper]) {
      readout.removeAttribute('hidden');
      readout.style.display = 'inline';
    }
    current.style.visibility = 'visible';
    return {
      channel, strip, slider, ticks, y: Number(slider.dataset.y),
      fill: strip.querySelector('.level-fill'),
      current, upper, lower, valueText: current.querySelector('.rv'), levelText: current.querySelector('.rl')
    };
  });

  function textInterval(readout) {
    const value = readout.querySelector('.rv').getBoundingClientRect();
    const label = readout.querySelector('.rl').getBoundingClientRect();
    return {left: Math.min(value.left, label.left), right: Math.max(value.right, label.right)};
  }

  // The printed full-level state has three readings above and two below a channel.
  // Keep all five, and move only the type when adjacent values fall close together.
  function setTextOffset(group, dx) {
    const value = group.querySelector('.rv');
    const high = Number(value.getAttribute('y')) < 0;
    if (!group.hasAttribute('data-rest-offset')) group.dataset.restOffset = value.getAttribute('x');
    value.setAttribute('x', String(dx));
    group.querySelector('.rl').setAttribute('x', String(dx));
    group.querySelector('.readout-stem').setAttribute('d', 'M0 ' + (high ? -2.6 : 3.6) + 'l' + dx + ' ' + (high ? -4.6 : 4.8));
  }

  function arrangePrintedLane(groups) {
    const edge = 2.5, gap = 4;
    const labels = groups.slice().sort((a, b) => Number(a.dataset.x) - Number(b.dataset.x)).map(group => {
      group.style.visibility = 'visible';
      const half = Math.max(...Array.from(group.querySelectorAll('text'), text => text.getBBox().width)) / 2;
      const anchor = Number(group.dataset.x);
      const preferred = anchor + Number(group.dataset.restOffset ?? group.querySelector('.rv').getAttribute('x'));
      return {group, half, anchor, center: preferred};
    });
    let left = edge;
    for (const item of labels) {
      item.center = Math.max(item.center, left + item.half);
      left = item.center + item.half + gap;
    }
    let right = svg.viewBox.baseVal.width - edge;
    for (const item of labels.reverse()) {
      item.center = Math.min(item.center, right - item.half);
      right = item.center - item.half - gap;
      setTextOffset(item.group, Number((item.center - item.anchor).toFixed(2)));
    }
  }

  function updateVisibility(row, x) {
    if (x === row.ticks[cap - 1].x) {
      arrangePrintedLane(row.upper);
      const capEcho = row.strip.querySelector('.read-max');
      capEcho.style.visibility = 'hidden';
      arrangePrintedLane([...row.lower.filter(g => g !== capEcho), row.current]);
      return;
    }
    for (const group of [...row.upper, ...row.lower, row.current]) {
      if (group.hasAttribute('data-rest-offset')) setTextOffset(group, Number(group.dataset.restOffset));
    }

    const gap = Math.abs(svg.getScreenCTM().a);
    function place(readouts, occupied, omitSelected) {
      for (const readout of readouts) {
        const span = textInterval(readout);
        const duplicate = omitSelected && Math.abs(Number(readout.dataset.x) - x) < .01;
        const overlaps = occupied.some(other => span.left < other.right + gap && other.left < span.right + gap);
        readout.style.visibility = duplicate || overlaps ? 'hidden' : 'visible';
        if (!duplicate && !overlaps) occupied.push(span);
      }
    }
    place(row.upper, [], true);
    place(row.lower, [textInterval(row.current)], false);
  }

  let level;
  function select(next) {
    if (next === level) return;
    level = next;
    let selectedTotal = 0;
    for (const row of rows) {
      const value = row.channel.levels[level - 1];
      const x = row.ticks[level - 1].x;
      selectedTotal += value;
      row.slider.setAttribute('transform', 'translate(' + x + ',' + row.y + ')');
      row.slider.dataset.level = String(level);
      row.slider.dataset.value = String(value);
      row.fill.setAttribute('x2', String(x));
      row.current.setAttribute('transform', 'translate(' + x + ',' + row.current.dataset.y + ')');
      row.current.dataset.x = String(x);
      row.current.dataset.value = String(value);
      row.current.dataset.level = String(level);
      row.valueText.textContent = format.format(value);
      row.levelText.textContent = 'Lv.' + level;
      updateVisibility(row, x);
      row.strip.setAttribute('aria-valuenow', String(level));
      row.strip.setAttribute('aria-valuetext', 'Lv.' + level + '，' + row.channel.label + ' ' + format.format(value));
    }
    window.updateMasterAxis?.(level, selectedTotal);
  }

  for (const row of rows) {
    row.strip.addEventListener('keydown', event => {
      let next;
      switch (event.key) {
        case 'ArrowLeft': next = Math.max(1, level - 1); break;
        case 'ArrowRight': next = Math.min(cap, level + 1); break;
        case 'Home': next = 1; break;
        case 'End': next = cap; break;
        default: return;
      }
      event.preventDefault();
      select(next);
    });
  }

  function point(event) {
    return new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
  }
  function pickLevel(row, x) {
    let closest = row.ticks[0];
    for (const tick of row.ticks) {
      if (Math.abs(tick.x - x) < Math.abs(closest.x - x)) closest = tick;
    }
    select(closest.level);
  }

  // The intervals between row centers are also live, including unpainted tick gaps.
  const sortedRows = [...rows].sort((a, b) => a.y - b.y);
  const top = sortedRows[0].y - (sortedRows[1].y - sortedRows[0].y) / 2;
  const last = sortedRows.length - 1;
  const bottom = sortedRows[last].y + (sortedRows[last].y - sortedRows[last - 1].y) / 2;
  let active = null;
  svg.style.touchAction = 'none';
  svg.addEventListener('pointerdown', event => {
    if (active || event.button !== 0 || !event.isPrimary) return;
    const {x, y} = point(event);
    const painted = rows.find(row => row.strip.contains(event.target));
    if (!painted && (y < top || y > bottom)) return;
    const row = painted || rows.reduce((near, r) => Math.abs(r.y - y) < Math.abs(near.y - y) ? r : near);
    active = {id: event.pointerId, row};
    svg.setPointerCapture(event.pointerId);
    row.strip.focus({preventScroll: true});
    event.preventDefault();
    pickLevel(row, x);
  });
  svg.addEventListener('pointermove', event => {
    if (active?.id === event.pointerId) pickLevel(active.row, point(event).x);
  });
  svg.addEventListener('pointerup', event => {
    if (active?.id !== event.pointerId) return;
    pickLevel(active.row, point(event).x);
    active = null;
  });
  function cancel(event) {
    if (active?.id === event.pointerId) active = null;
  }
  svg.addEventListener('pointercancel', cancel);
  svg.addEventListener('lostpointercapture', cancel);
  select(cap);
  // Exported HTML can open before its local fonts finish loading.
  document.fonts.ready.then(() => {
    for (const row of rows) updateVisibility(row, row.ticks[level - 1].x);
  });
  svg[registrations] = true;
};

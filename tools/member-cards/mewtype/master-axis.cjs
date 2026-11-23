'use strict';

function masterAxis(channels, growth) {
  const initial = channels.reduce((sum, c) => sum + c.levels[0], 0);
  const base = channels.reduce((sum, c) => sum + c.levels.at(-1), 0);
  const full = growth.total;
  const trained = base + growth.trainingBonus;
  // Insets leave room for the endpoint numbers when they have six digits and a comma.
  const left = 51, right = 197, railY = 45;
  const x = value => left + (value - initial) / (full - initial) * (right - left);
  const fmt = value => value.toLocaleString('en-US');
  const baseX = x(base).toFixed(3), trainedX = x(trained).toFixed(3);
  return `<svg class="master-axis" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 242 100"
      role="img" aria-label="综合力：Lv.1 ${fmt(initial)}，Lv.${growth.maxLevel} ${fmt(base)}，满培养 ${fmt(full)}"
      style="display:block;width:100%;height:auto;font-variant-numeric:tabular-nums"
      data-initial="${initial}" data-full="${full}" data-x0="${left}" data-x1="${right}" data-rail-y="${railY}"
      font-family="LXGW WenKai, sans-serif" fill="none" stroke-linecap="round">
    <text x="2.8" y="${railY+3.75}" font-size="12.7" font-weight="600" fill="#3d425f">综合力</text>

    <g class="master-initial" text-anchor="middle">
      <text x="${left}" y="12.2" font-size="11.2" fill="#546379">Lv.1</text>
      <text x="${left}" y="33.45" font-size="18" fill="#43536b">${fmt(initial)}</text>
      <path d="M${left} 37.1v4.7" stroke="#8c9cb1" stroke-width=".8"/>
    </g>
    <g class="master-full" text-anchor="middle">
      <text x="${right}" y="12.2" font-size="11.8" font-weight="600" fill="#795625">满培养</text>
      <path d="M159.7 31.2l74.1-.65-.8 5.55-74.9.55Z" fill="#efd38b"/>
      <text class="master-full-value" x="${right}" y="33.75" font-size="20" font-weight="600" fill="#503d42">${fmt(full)}</text>
      <path d="M${right} 37.4v4.3" stroke="#ba924c" stroke-width=".8"/>
    </g>

    <path d="M44.6 ${railY}H218m-2.3-1.75 2.3 1.75-2.3 1.75" stroke="#8494b0" stroke-width=".8"/>
    <path d="M${left} ${railY}H${baseX}" stroke="#acb9d2" stroke-width="2.3"/>
    <line class="master-fill" x1="${left}" x2="${baseX}" y1="${railY}" y2="${railY}" stroke="#667ca4" stroke-width="2.3"/>
    <path d="M${baseX} ${railY}H${right}" stroke="#bd8b43" stroke-width="2.3"/>
    <circle cx="${left}" cy="${railY}" r="2.3" fill="#667ca4"/>
    <path class="master-trained-mark" data-value="${trained}" d="M${trainedX} ${railY-3.3}v6.6" stroke="#97703d" stroke-width="1.45"/>
    <path class="master-full-mark" data-value="${full}" d="M${right} ${railY-3.55}l3.6 3.55-3.6 3.55-3.6-3.55Z" fill="#c09040" stroke="#805c2c" stroke-width=".65"/>

    <g class="master-current" data-level="${growth.maxLevel}" transform="translate(${baseX} ${railY})">
      <path d="M-2.7-4.25h5.3l.55.85v6.3l-3.1 2.2-3.05-2.2v-6.3Z" fill="#677d9f" stroke="#425a7d" stroke-width=".7"/>
      <path d="M0-3v6.1" stroke="#e8f4fb" stroke-width=".75"/>
      <path d="M0 6.15v2.65" stroke="#8c9cae" stroke-width=".7"/>
      <text x="0" y="25.6" text-anchor="middle" fill="#3e4b6d"><tspan class="master-level" font-size="11.5">Lv.${growth.maxLevel}</tspan><tspan class="master-value" dx="3.5" font-size="18.8" font-weight="600">${fmt(base)}</tspan></text>
    </g>

    <path d="M3.8 78.6h234" stroke="#91a3b980" stroke-width=".65"/>
    <g fill="#52617b" font-size="12.1">
      <path d="M2 89.7h6.5m-3.25-2.65v5.3" stroke="#97703d" stroke-width="1.1"/>
      <text class="master-training-note" x="11.1" y="94.15">特训 ${growth.trainingStage-1} 次<tspan dx="3" fill="#3e4b6d" font-size="12.7" font-weight="500">+${fmt(growth.trainingBonus)}</tspan></text>
      <path d="m126.1 86.7 2.95 3-2.95 3-2.95-3Z" fill="#b98237" stroke="#755835" stroke-width=".55"/>
      <text class="master-awakening-note" x="132.15" y="94.15">觉醒 ${growth.awakeningStage-1} 次<tspan dx="3" fill="#3e4b6d" font-size="12.7" font-weight="500">+${fmt(growth.awakeningBonus)}</tspan></text>
    </g>
  </svg>`;
}

function updateMasterAxis(svg, level, selectedTotal) {
  const initial = Number(svg.dataset.initial), full = Number(svg.dataset.full);
  const left = Number(svg.dataset.x0), right = Number(svg.dataset.x1);
  const x = left + (selectedTotal - initial) / (full - initial) * (right - left);
  const current = svg.querySelector('.master-current');
  current.setAttribute('transform', 'translate(' + x.toFixed(3) + ' ' + svg.dataset.railY + ')');
  current.dataset.level = String(level);
  current.querySelector('.master-level').textContent = 'Lv.' + level;
  current.querySelector('.master-value').textContent = selectedTotal.toLocaleString('en-US');
  svg.querySelector('.master-fill').setAttribute('x2', x.toFixed(3));
  svg.querySelector('.master-initial').style.visibility = level === 1 ? 'hidden' : 'visible';
  svg.setAttribute('aria-label', '综合力，Lv.' + level + ' ' + selectedTotal.toLocaleString('en-US') + '，满培养 ' + full.toLocaleString('en-US'));
}

module.exports = masterAxis;
module.exports.update = updateMasterAxis;

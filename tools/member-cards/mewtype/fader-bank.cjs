'use strict';

// Each channel uses the same attribute-value axis. Level positions come from
// this card's authored level table, and the two cultivation contributions stack above it.
function valueScale(channels) {
  const first = Math.min(...channels.map(c => c.levels[0]));
  const last = Math.max(...channels.map(c => c.full));
  const target = Math.max(1, (last - first) / 5);
  const power = 10 ** Math.floor(Math.log10(target));
  const steps = [1, 2, 2.5, 5, 10].map(multiplier => power * multiplier);
  const interval = Math.max(1, steps.reduce((a, b) =>
    Math.abs(a - target) <= Math.abs(b - target) ? a : b));
  const min = Math.floor(first / interval) * interval;
  const max = Math.max(min + interval, Math.ceil(last / interval) * interval);
  const x = value => Number((38 + (value - min) / (max - min) * 192).toFixed(3));
  return {min, max, x};
}

function labelledLevels(maxLevel) {
  const nearestTen = value => Math.round(value / 10) * 10;
  return new Set([1, nearestTen(maxLevel/3), nearestTen(2*maxLevel/3), maxLevel]
    .filter(level => level >= 1 && level <= maxLevel));
}

function readout(name, x, y, value, label, above, hidden=false) {
  const numY = above ? -10 : 24;
  const labelY = above ? -28 : 37;
  const at = Number(x.toFixed(3));
  // Keep long endpoint readouts within the faceplate. The diamond stays at its exact value.
  const dx = name === 'read-full' ? Number((Math.min(208, at) - at).toFixed(3)) : 0;
  return `<g class="${name} readout" data-x="${at}" data-y="${y}" transform="translate(${at} ${y})"${hidden?' style="display:none"':''}>
    <path d="M0 ${above?-2.6:3.6}l${dx} ${above?-3:3.4}" class="readout-stem"/>
    <text class="rv" x="${dx}" y="${numY}">${value.toLocaleString('en-US')}</text>
    <text class="rl" x="${dx}" y="${labelY}">${label}</text>
  </g>`;
}

function channel(c, i, scale) {
  const y=42+i*83;
  const first=scale.x(c.initial), base=scale.x(c.base);
  const trained=scale.x(c.base+c.trainingBonus), full=scale.x(c.full);
  const keys=labelledLevels(c.levels.length);
  const graduations=c.levels.map((value,index)=>{
    const level=index+1;
    const marked=keys.has(level), ten=level%10===0;
    if(!marked && level%5!==0) return '';
    const x=scale.x(value);
    const tickHeight=marked?3.6:ten?2.6:1.6;
    return `<g class="graduation" data-level="${level}" data-value="${value}" data-x="${x}"><path d="M${x} ${y+3.1}v${tickHeight}" stroke="${marked?'#7d8da2':'#95a3b4'}" stroke-width="${marked?.8:.6}"/></g>`;
  }).join('');
  return `<g class="strip" data-key="${c.key}">
    <rect class="hit-area" x="0" y="${y-39}" width="242" height="80" fill="transparent"/>
    <g transform="rotate(${[-1.7,.7,-.85][i]} 17 ${y})">
      <path d="M2.8 ${y-9}l29.2-.25.9 1.3-.55 15-30 .3Z" fill="${c.color}" fill-opacity=".39" stroke="#9aabc0" stroke-width=".65"/>
      <path d="M4.2 ${y-7.5}h26" stroke="#f9fcff" stroke-width=".7" opacity=".7"/>
      <text x="17.5" y="${y+4.2}" text-anchor="middle" fill="#313c51" font-family="LXGW WenKai,sans-serif" font-size="13.8" font-weight="600">${c.label}</text>
    </g>
    <rect x="36.5" y="${y-1.6}" width="195.5" height="4.5" rx="2.1" fill="#fbfdff"/>
    <rect x="36.5" y="${y-2.4}" width="195.5" height="4.55" rx="2.2" fill="#9daec2"/>
    <path d="M38 ${y}h192" stroke="#596b84" stroke-width=".65"/>
    <line x1="${first}" x2="${base}" y1="${y}" y2="${y}" stroke="${c.color}" stroke-width="2.5" stroke-opacity=".22"/>
    <line class="level-fill" x1="${first}" x2="${base}" y1="${y}" y2="${y}" stroke="${c.color}" stroke-width="2.5"/>
    <line x1="${base}" x2="${full}" y1="${y}" y2="${y}" stroke="#ae8441" stroke-width="2"/>
    <circle cx="${first}" cy="${y}" r="2.15" fill="${c.color}" stroke="#302733" stroke-width=".6"/>
    <path class="training-stop" data-value="${c.base+c.trainingBonus}" d="M${trained} ${y-3.25}v6.5" stroke="#90662e" stroke-width="1.6"/>
    <path class="full-stop" data-value="${c.full}" d="M${full} ${y-3.1}l3.25 3.1-3.25 3.1-3.25-3.1Z" fill="#bd8c42" stroke="#855c2b" stroke-width=".5"/>
    ${graduations}
    ${readout('read-initial', first, y, c.initial, 'Lv.1', true)}
    ${readout('read-full', full, y, c.full, '满培养', true)}
    ${[...keys].filter(level=>level!==1 && level!==c.levels.length).sort((a,b)=>a-b).map((level,i)=>{
      const value=c.levels[level-1], x=scale.x(value), above=(i===1);
      const crowded = above ? (x-first < 43 || full-x < 49) : (base-x < 44);
      return readout(above?'read-mid-high':'read-mid-low',x,y,value,'Lv.'+level,above,crowded);
    }).join('')}
    ${readout('read-max',base,y,c.base,'Lv.'+c.levels.length,false,true)}
    ${readout('read-current',base,y,c.base,'Lv.'+c.levels.length,false)}
    <g class="slider" data-y="${y}" data-value="${c.base}" transform="translate(${base} ${y})">
      <rect x="-4.7" y="-6.6" width="11.4" height="15.6" rx="2" fill="#524150" opacity=".41"/>
      <path d="M-4.3-7.9h8.5q1.35.15 1.4 1.65l.42 11.85q0 1.55-1.5 1.63h-9q-1.3-.2-1.3-1.55l.25-11.9q.1-1.4 1.23-1.68Z" fill="${c.color}" stroke="#6b5669" stroke-width=".75"/>
      <path d="M-4.2-7.25h8.35l.6 13.5h-9.25Z" fill="url(#cap-sheen)"/>
      <path d="M-3.9-6.4h7.8m-7.6 10.9h7.4" stroke="#fff4db" stroke-width=".7" opacity=".73"/>
      <path d="M-3.1-4.9V3.5m6.25-8.4v8.4" stroke="#6b5362" stroke-width=".55" opacity=".68"/>
      <path d="M0-7.3v13.8" stroke="#473342" stroke-width="1.2"/>
      <path d="M.8-7.2v13.7" stroke="#fff2d0" stroke-width=".5"/>
    </g>
  </g>`;
}

module.exports=function faderBank(channels) {
  const scale=valueScale(channels);
  return `<svg class="fader-bank" data-min="${scale.min}" data-max="${scale.max}" data-xmin="${scale.x(scale.min)}" data-xmax="${scale.x(scale.max)}" viewBox="0 0 242 251" xmlns="http://www.w3.org/2000/svg" fill="none" stroke-linejoin="round" font-family="Roboto Variable,Arial,sans-serif">
    <defs><linearGradient id="cap-sheen" x2="1" y2=".1"><stop stop-color="#fff3d7" stop-opacity=".45"/><stop offset=".4" stop-color="white" stop-opacity=".04"/><stop offset=".77" stop-color="#392a3b" stop-opacity=".16"/><stop offset="1" stop-color="#382a38" stop-opacity=".42"/></linearGradient></defs>
    <path d="M2 84h237m-237 83h237" stroke="#96a7bd" stroke-width=".55" opacity=".6"/>
    ${channels.map((c,i)=>channel(c,i,scale)).join('')}
  </svg>`;
}

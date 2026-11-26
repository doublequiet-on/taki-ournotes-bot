module.exports = function finishMixer(channels) {
  const bank = document.querySelector('.foldsheet[data-band-id="3"] .fader-bank');
  if (!bank) return;
  const master = document.querySelector('.master-axis');

  function addMaterials(svg, prefix) {
    svg.insertAdjacentHTML('afterbegin', `<defs>
      <linearGradient id="${prefix}-track" x1="0" x2="0" y1="0" y2="1">
        <stop stop-color="#443f52"/><stop offset=".42" stop-color="#635c71"/><stop offset=".78" stop-color="#878093"/><stop offset="1" stop-color="#b5afc0"/>
      </linearGradient>
      <linearGradient id="${prefix}-cap" x1="0" y1=".1" x2=".88" y2="1">
        <stop stop-color="#fffefa"/><stop offset=".23" stop-color="#f2eff3"/><stop offset=".51" stop-color="#c8c4d1"/>
        <stop offset=".8" stop-color="#e2dfe8"/><stop offset="1" stop-color="#b4aebb"/>
      </linearGradient>
    </defs>`);
  }

  function slot(start,end,y,prefix) {
    const width=end-start;
    return `<g aria-hidden="true">
      <rect x="${start-2.4}" y="${y-3.1}" width="${width+4.8}" height="7.3" rx="3.5" fill="#fff9fa" stroke="#b4a9b7" stroke-width=".6"/>
      <rect x="${start-1.25}" y="${y-2.8}" width="${width+2.5}" height="5.7" rx="2.75" fill="url(#${prefix}-track)"/>
      <path d="M${start} ${y-1.5} h${width}" stroke="#383244" stroke-opacity=".73" stroke-width="1.2"/>
    </g>`;
  }

  function cap(prefix, scale) {
    return `<g class="grip" transform="scale(${scale})" aria-hidden="true">
      <rect x="-7" y="-4.6" width="18" height="14" rx="3.2" fill="#392942" opacity=".17"/>
      <path d="M-8-5.6q0-1.5 1.6-1.5H6.5L9-4.8V5.8L7.2 8H-5.7L-8 5.9Z" fill="#82778d" stroke="#594f62" stroke-width=".8"/>
      <path d="M-8-5.7q0-1.7 1.6-1.8H6.3q1.4 0 1.5 1.5l-.25 9.6L6.1 5.2H-6.6L-8 3.8Z" fill="url(#${prefix}-cap)" stroke="#8b8094" stroke-width=".6"/>
      <path d="M-6.4-6.5H6M-7.1-5.7v8.8" fill="none" stroke="#fffaf7" stroke-width="1.05"/>
      <path d="M-6.2 5.4H6.2l1.4-1.7" fill="none" stroke="#5c5267" stroke-width=".85"/>
      <path d="M-5.2-3.8v6.1m2-6.1v6.1m6.5-6.1v6.1m2-6.1v6.1" stroke="#857c90" stroke-width=".85" stroke-linecap="round"/>
      <path d="M-4.6-3.6v5.7m2-5.7v5.7m6.5-5.7v5.7m2-5.7v5.7" stroke="#fff8fa" stroke-width=".6"/>
      <rect x="-1.35" y="-6.4" width="2.7" height="11.8" rx=".65" fill="currentColor" stroke="#605166" stroke-width=".55"/>
      <path d="M0 6.1v1.4" stroke="#514355" stroke-width=".95"/>
    </g>`;
  }

  addMaterials(bank,'channel');
  const colors = new Map(channels.map(c=>[c.key,c.color]));
  for (const strip of bank.querySelectorAll('.strip')) {
    const slider = strip.querySelector('.slider');
    const y = Number(slider.dataset.y);
    strip.querySelector('.level-fill').insertAdjacentHTML('beforebegin',
      slot(Number(bank.dataset.xmin),Number(bank.dataset.xmax),y,'channel'));
    slider.style.color = colors.get(strip.dataset.key);
    slider.innerHTML = cap('channel',1);
  }
  addMaterials(master,'master');
  master.querySelector('.master-fill').insertAdjacentHTML('beforebegin',
    slot(Number(master.dataset.x0),Number(master.dataset.x1),Number(master.dataset.railY),'master'));
  const current = master.querySelector('.master-current');
  for (const shape of current.querySelectorAll(':scope > path')) shape.remove();
  current.style.color = '#9badd8';
  current.insertAdjacentHTML('afterbegin',cap('master',.78));
};

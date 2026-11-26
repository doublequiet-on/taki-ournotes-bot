module.exports = function installFilterPanel({skills, liveNotes}) {
  const leaf = document.querySelector('.foldsheet[data-band-id="3"] .lyric-page');
  if (!leaf) return;
  const list = leaf.querySelector(':scope > .skills');
  const title = leaf.querySelector(':scope > h2.section');
  const panel = document.createElement('div');
  panel.className = 'filter-panel';
  title.before(panel);
  panel.append(title, list);
  title.querySelector('.levels-label')?.remove();

  function tierGuide(sequence, label) {
    const guide = document.createElement('span');
    guide.className = 'tier-guide';
    if (label) {
      label.classList.add('field-key');
      guide.append(label);
    }
    const ticks = document.createElement('span');
    ticks.className = 'tier-captions';
    for (let tier = 1; tier <= sequence.children.length; tier++) {
      const tick = document.createElement('span');
      tick.textContent = 'Lv.' + tier;
      ticks.append(tick);
    }
    guide.append(ticks);
    return guide;
  }

  for (const [index, row] of [...list.children].entries()) {
    const skill = skills[index];
    const head = row.querySelector('.skillhead');
    const rail = document.createElement('div');
    rail.className = 'filter-rail';
    const icon = row.querySelector(':scope > .skill-icon');
    if (icon) rail.append(icon);
    rail.append(head.querySelector('.kind'));
    const body = document.createElement('div');
    body.className = 'filter-body';
    body.append(...row.children);
    row.append(rail, body);

    // The upstream renderer has already validated this two-condition ladder.
    // Separate the duration only when the localized caption also round-trips.
    const ladder = body.querySelector('.ladder');
    if (ladder) {
      const caption = ladder.querySelector('.shared-duration');
      const live = liveNotes[skill.skillId];
      const limits = ladder.querySelector('.ladder-rows');
      let key;
      if (ladder.lang === 'zh' && live?.type === 'lifeScore' &&
          caption.textContent === live.seconds + ' 秒内得分提升') {
        const field = document.createElement('div');
        field.className = 'duration-field';
        const name = document.createElement('span');
        name.textContent = '持续时间';
        const time = document.createElement('span');
        time.className = 'readout';
        time.textContent = live.seconds + ' 秒';
        field.append(name,time);
        caption.replaceWith(field);
        key = document.createElement('span');
        key.textContent = '得分提升';
      }
      limits.before(tierGuide(limits.querySelector('.sequence'), key));
    }
    for (const effect of body.querySelectorAll('.effect')) {
      const sequence = effect.querySelector(':scope > .sequence');
      if (sequence) {
        const label = effect.querySelector(':scope > .duration-label');
        sequence.before(tierGuide(sequence, label));
      }
    }
    const table = body.querySelector('table.score-pair');
    if (table) {
      const tierCount = table.tBodies[0].rows[0].querySelectorAll('.level').length;
      const heading = table.createTHead().insertRow();
      heading.append(document.createElement('td'));
      for (let tier = 1; tier <= tierCount; tier++) {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = 'Lv.' + tier;
        heading.append(th);
      }
    }
  }
  // A long points readout may need more room than five percentages. Grow the
  // flexible middle leaf while keeping the type size and both outside folds.
  let extra = 0;
  const ink = document.createRange();
  for (const value of list.querySelectorAll('.level')) {
    ink.selectNodeContents(value);
    const tierCount = value.closest('.sequence, tr').querySelectorAll('.level').length;
    extra = Math.max(extra, tierCount * (ink.getBoundingClientRect().width + 3 - value.offsetWidth));
  }
  if (extra > 0) {
    const sheet = leaf.closest('main');
    sheet.style.width = Math.ceil(sheet.getBoundingClientRect().width + extra) + 'px';
  }
};

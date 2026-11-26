function applyFacts(facts) {
  const sheet = document.querySelector('main[data-band-id="2"]');
  const table = sheet.querySelector('table.stats');
  const fmt = value => value.toLocaleString('en-US');
  const growth = facts.growth;
  table.tHead.rows[0].cells[0].textContent = '属性值';
  table.tHead.rows[0].appendChild(document.createElement('th')).textContent = '满培养';
  const rows = [...table.tBodies[0].rows];
  const sums = [0, 0, 0];
  rows.forEach((row, i) => {
    const values = growth.stats[i];
    if (row.cells[0].textContent.trim() !== facts.labels[i]) throw Error('Stat order changed');
    row.insertCell();
    [values.initial, values.base, values.full].forEach((v, col) => {
      sums[col] += v;
      row.cells[col + 1].textContent = fmt(v);
    });
  });
  if (sums[1] + growth.trainingBonus + growth.awakeningBonus !== sums[2]) throw Error('Growth total mismatch');
  const total = table.tFoot.rows[0];
  total.insertCell();
  sums.forEach((value, i) => total.cells[i + 1].textContent = fmt(value));
  sheet.querySelector('.art-page .plate-illustration').remove();
  if (facts.debut) {
    const date = document.createElement('time');
    date.className = 'debut';
    date.dateTime = facts.debut;
    date.textContent = '实装时间 ' + facts.debut.replaceAll('-', '.');
    sheet.querySelector('.traits').append(date);
  }
  const articles = [...sheet.querySelectorAll('.skills article')];
  if (articles.length !== facts.articles.length) throw Error('Skill count changed');
  for (const [i, article] of articles.entries()) {
    const original = facts.articles[i];
    const head = article.querySelector('.skillhead');
    head.querySelector('h3').textContent = original.name;
    const icon = article.querySelector('.skill-icon');
    article.replaceChildren(...[icon, head].filter(Boolean));
    const body = document.createElement('div');
    body.className = 'clauses ave-source';
    article.append(body);
    let first = 1;
    for (const group of original.groups) {
      if (original.groups.length > 1) {
        const range = document.createElement('small');
        range.textContent = 'Lv.' + first + (first === group.sample ? '' : '–' + group.sample);
        body.append(range);
      }
      const visible = [];
      for (const part of group.parts) {
        const effect = document.createElement('div');
        const condition = document.createElement('div');
        condition.className = 'condition';
        const reading = document.createElement('em');
        reading.textContent = part.reading;
        condition.append(part.before, reading, part.after);
        effect.append(condition);
        const levels = [];
        if (part.values.length) {
          const track = document.createElement('div');
          track.className = 'sequence';
          if (/(?:LIFE|生命值)在$/i.test(part.before) && part.after.startsWith('以上时') && /^\d+$/.test(part.reading)) {
            const caption = document.createElement('div');
            caption.className = 'ave-track-caption';
            caption.textContent = 'LIFE 门槛';
            effect.append(caption);
          }
          for (let level = first; level <= group.sample; ++level) {
            const value = part.values[level - 1];
            if (!value) throw Error('Missing skill reading at Lv.' + level);
            const cell = document.createElement('span');
            cell.className = 'level';
            cell.dataset.level = level;
            cell.textContent = value;
            levels.push(cell);
            track.append(cell);
          }
          effect.append(track);
        }
        visible.push({condition, reading, levels});
        body.append(effect);
      }
      for (let lvl = first; lvl <= group.sample; ++lvl) {
        const text = visible.map(({condition,reading,levels}) =>
          [...condition.childNodes].map(node => node === reading && levels.length ?
            levels[lvl-first].textContent : node.textContent).join('')).join('\n');
        if (text !== original.texts[lvl-1]) throw Error('Skill wording mismatch at level ' + lvl);
      }
      first = group.sample + 1;
    }
  }
}
module.exports = applyFacts;

module.exports = async function polishEnsemble(assetBase) {
  const sheet = document.querySelector('main.foldsheet[data-band-id="3"]');
  if (!sheet) return;
  const title = sheet.querySelector('.lyric-page h1');
  if (title.childElementCount === 0 && title.textContent.trim()) {
    const phrase = document.createElement('span');
    phrase.className = 'title-phrase';
    phrase.append(...title.childNodes);
    title.append(phrase);
  }
  const shelf = sheet.querySelector('.band-shelf');
  const member = new URL(shelf.src).hash;
  shelf.src = new URL('band-shelf.svg' + member, assetBase).href;
  const pet = sheet.querySelector('.maru');
  pet.src = new URL('maru-edge.svg', assetBase).href;
  pet.alt = '丸君把前爪搭在任务栏边缘';
  for (const unit of sheet.querySelectorAll('.skills h3 .keep')) {
    if (!/^&?激奏技能$/.test(unit.textContent)) continue;
    const br = unit.nextElementSibling;
    const qualifier = br?.nextElementSibling;
    if (br?.localName !== 'wbr' || !qualifier?.matches('.keep') ||
        !/^【[^】]+】$/.test(qualifier.textContent)) continue;
    unit.classList.add('skill-family');
    unit.append(qualifier.textContent);
    br.remove();
    qualifier.remove();
  }
  await Promise.all([shelf.decode(),pet.decode(),document.fonts.ready]);
  let extra = 0;
  for (const table of sheet.querySelectorAll('.bonus-table')) {
    const ink = new Range();
    let label = 0, number = 0;
    for (const row of table.rows) {
      for (const cell of row.cells) {
        ink.selectNodeContents(cell);
        const width = Math.ceil(ink.getBoundingClientRect().width);
        if (cell.cellIndex === 0) label = Math.max(label,width);
        else number = Math.max(number,width);
      }
    }
    const labelWidth = Math.max(54,label + 5);
    table.style.setProperty('--row-label',labelWidth+'px');
    const count = table.rows[0].cells.length - 1;
    extra = Math.max(extra,labelWidth + count*(number+2) - table.clientWidth);
  }
  if (extra > 0) {
    const right = sheet.querySelector('.relation-page').offsetWidth;
    const width = sheet.offsetWidth;
    sheet.style.setProperty('--right',(right+extra)+'px');
    sheet.style.width = (width+extra)+'px';
  }
};

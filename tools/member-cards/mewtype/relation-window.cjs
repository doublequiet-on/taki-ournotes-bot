module.exports = function arrangeRelationWindows(cardId) {
  const page = document.querySelector('main.foldsheet[data-band-id="3"] > .relation-page');
  if (!page) return;
  const make = (tag, className) => {
    const element = document.createElement(tag);
    element.className = className;
    return element;
  };
  for (const group of page.querySelectorAll('.relations > div')) {
    const entries = group.querySelectorAll(':scope > dd');
    if (!entries.length) continue;
    const section = group.classList.contains('events') ? '活动' : '卡池';
    group.classList.add('web-window');
    const title = group.querySelector(':scope > dt');
    const tab = make('span','browser-tab');
    tab.append(...title.childNodes);
    title.append(tab);
    for (const entry of entries) {
      const caption = entry.querySelector('.relation-caption');
      const id = caption.querySelector('.relation-id');
      const locator = make('div','browser-toolbar');
      const address = make('div','record-address');
      const card = make('span','address-card');
      card.textContent = '卡 ' + cardId;
      const record = make('span','address-record');
      record.append(section + ' ', id);
      address.append(card, record);
      locator.append(address);
      const body = make('div','window-content');
      const image = entry.querySelector(':scope > .clipping, :scope > .relation-banner');
      if (image) body.append(image);
      body.append(...entry.children);
      entry.append(locator, body);
    }
  }
  // Fractional bonuses sometimes require more space than the base fold.
  // Keep the numeric cells intact and give the extra width to the right leaf.
  const sheet = page.closest('main');
  let extraWidth = 0;
  for (const table of page.querySelectorAll('.bonus-table')) {
    const body = table.closest('.window-content');
    const contentWidth = body.clientWidth - parseFloat(getComputedStyle(body).paddingLeft)
      - parseFloat(getComputedStyle(body).paddingRight);
    extraWidth = Math.max(extraWidth, table.getBoundingClientRect().width - contentWidth);
  }
  if (extraWidth > .5) {
    const extra = Math.ceil(extraWidth);
    const sheetWidth = sheet.getBoundingClientRect().width;
    sheet.style.setProperty('--right', (page.getBoundingClientRect().width + extra) + 'px');
    sheet.style.width = (sheetWidth + extra) + 'px';
  }
};

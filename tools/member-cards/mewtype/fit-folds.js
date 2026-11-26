// Let the widest authored five-level row set the minimum writing area.
window.foldReady = (async () => {
  await document.fonts.ready;
  await Promise.all(Array.from(document.images, image => image.decode()));
  const main = document.querySelector('main.foldsheet');
  const contentWidth = element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return range.getBoundingClientRect().width;
  };
  const levels = [...main.querySelectorAll('.level')];
  const section = main.querySelector('.skills');
  let extra = 0;
  for (const level of levels) {
    const cell = level.closest('td') || level;
    extra = Math.max(extra, (contentWidth(level) + 3 - cell.getBoundingClientRect().width) * 5);
  }
  const related = main.querySelector('.relation-page');
  let rightGrowth = 0;
  if (related) {
    for (const table of related.querySelectorAll('.bonus-table')) {
      const width = table.parentElement.getBoundingClientRect().width;
      rightGrowth = Math.max(rightGrowth, table.getBoundingClientRect().width - width);
    }
  }
  if (extra > 0 || rightGrowth > 0) {
    const width = main.getBoundingClientRect().width;
    if (rightGrowth > 0) main.style.setProperty('--right', (related.offsetWidth + Math.ceil(rightGrowth)) + 'px');
    main.style.width = (width + Math.ceil(extra) + Math.ceil(rightGrowth)) + 'px';
  }
  return !!section;
})();

module.exports = function installGachaAd() {
  const group = document.querySelector('.foldsheet[data-band-id="3"] .relation-page .gachas.web-window');
  if (!group) return;
  const entries = group.querySelectorAll(':scope > dd');
  const heading = group.querySelector(':scope > dt');
  const title = heading.querySelector('.browser-tab');
  const tag = document.createElement('span');
  group.classList.add('gacha-ad');
  title.className = 'ad-title';
  tag.className = 'ad-mark';
  tag.textContent = '广告';
  heading.prepend(tag);
  for (const entry of entries) {
    const locator = entry.querySelector(':scope > .browser-toolbar');
    const id = locator.querySelector('.relation-id');
    if (entries.length === 1) heading.append(id);
    else entry.querySelector('.relation-caption').append(id);
    locator.remove();
  }
};

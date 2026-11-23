module.exports = function arrangeStudio() {
  const header = document.querySelector('.foldsheet[data-band-id="3"] .lyric-page header');
  if (!header) return;
  const credit = header.querySelector('.onair-credit');
  const traits = header.querySelector('.traits');
  const band = traits.querySelector('.band');
  header.querySelector('.host-medallion')?.remove();
  if (band) credit.prepend(band);
  credit.append(traits);

  const stage = header.querySelector('.obs-preview');
  const titleBar = document.createElement('div');
  titleBar.className = 'studio-strip';
  const label = document.createElement('span');
  label.className = 'studio-label';
  label.textContent = '场景预览';
  titleBar.append(label, credit.querySelector('.tally'));
  stage.before(titleBar);
  const frame = stage.querySelector('.obs-selection');
  for (const position of ['n','e','s','w']) {
    const handle = document.createElement('i');
    handle.className = position;
    frame.append(handle);
  }
};

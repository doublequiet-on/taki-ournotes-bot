module.exports = async function installPreview() {
  const heading = document.querySelector('.foldsheet[data-band-id="3"] .lyric-page header h1');
  if (!heading) return;
  const stage = document.createElement('div');
  stage.className = 'obs-preview';
  const frame = document.createElement('span');
  frame.className = 'obs-selection';
  frame.setAttribute('aria-hidden', 'true');
  for (const corner of ['nw', 'ne', 'sw', 'se']) {
    const handle = document.createElement('i');
    handle.className = corner;
    frame.append(handle);
  }
  heading.before(stage);
  stage.append(heading, frame);
  await document.fonts.ready;
  function fitTitle() {
    // Only the visible text determines the frame. The available column remains
    // free to wrap a short or long title using the original title typography.
    const bounds = stage.getBoundingClientRect();
    const titleBox = heading.getBoundingClientRect();
    const walker = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    let right = titleBox.left;
    while (walker.nextNode()) {
      if (!walker.currentNode.textContent.trim()) continue;
      range.selectNodeContents(walker.currentNode);
      for (const run of range.getClientRects()) right = Math.max(right, run.right);
    }
    frame.style.left = (titleBox.left - bounds.left - 5) + 'px';
    frame.style.top = (titleBox.top - bounds.top - 3) + 'px';
    frame.style.width = (Math.min(right + 5, bounds.right - 2) - titleBox.left + 5) + 'px';
    frame.style.height = (titleBox.height + 7) + 'px';
  }
  stage.fitTitle = fitTitle;
  fitTitle();
  new ResizeObserver(fitTitle).observe(heading);
};

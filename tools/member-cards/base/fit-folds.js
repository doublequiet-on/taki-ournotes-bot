// Measure real wrapping once fonts and game art have loaded.
async function fitFolds() {
  await document.fonts.ready;
  await Promise.all([...document.images].map(async image => {
    try {
      await image.decode();
    } catch (error) {
      throw new Error(`Required card image could not be decoded: ${image.getAttribute('src')}`, {cause: error});
    }
  }));
  const sheet = document.querySelector('main');
  const panels = [...sheet.children];
  const related = !!sheet.querySelector('.relation-page');
  const width = sheet.clientWidth;
  const poolCount = sheet.querySelectorAll('.gachas .relation-banner').length;
  if (poolCount >= 3) sheet.classList.add('many-pools');
  const title = sheet.querySelector('h1');
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = '100px ' + getComputedStyle(title).fontFamily;
  const advance = measure.measureText(title.textContent).width / 100;
  // Short names can be a display title. Long names can take two lines at reading size.
  title.style.setProperty('--title-fit', (100 / Math.max(1, advance)) + 'cqi');
  const relatedList = sheet.querySelector('.relations');
  const eventNotes = sheet.querySelectorAll('.events dd:has(.event-targets)');
  sheet.classList.add('measuring');
  // Compare copy at a common measure, independent of artwork and optional folds.
  sheet.style.gridTemplateColumns = related ? '220px 280px 274px' : '240px 280px';
  const textLines = [...sheet.querySelectorAll('h1,h3,.condition')].reduce((count,el)=>{
    const range=document.createRange();
    range.selectNodeContents(el);
    const lines=new Set([...range.getClientRects()].filter(r=>r.width>0).map(r=>Math.round(r.top)));
    return count + lines.size;
  },0);
  const preferredSkillWidth = Math.min(348, Math.max(278, 248+textLines*4));
  let best = null;
  // A run of three full-colour pool banners needs a stronger cover on the first fold.
  const minCover = poolCount >= 3 ? 240 : related ? 228 : 200;
  // A short label beside the five level percentages needs more measure than
  // a row of numbers using the full fold. Measure its widest actual value.
  let minSkills = 260;
  const pair = sheet.querySelector('.score-pair');
  if (pair) {
    // Captioned and plain rows share the same five stops. Include the frame
    // insets and the widest value as drawn in its own font.
    const range = document.createRange();
    const widest = Math.max(...[...sheet.querySelectorAll('.skills .level')].map(cell => {
      range.selectNodeContents(cell);
      return range.getBoundingClientRect().width;
    }));
    const article = getComputedStyle(pair.closest('article'));
    const frameInset = parseFloat(article.paddingLeft) + parseFloat(article.paddingRight);
    const pane = getComputedStyle(sheet.querySelector('.lyric-page'));
    minSkills = Math.max(minSkills, Math.ceil(parseFloat(pane.paddingLeft) + parseFloat(pane.paddingRight)
       + frameInset + pair.querySelector('th').getBoundingClientRect().width + 5*(widest+5)));
  }
  const ladder = sheet.querySelector('.ladder-rows');
  if (ladder) {
    const cell = ladder.querySelector('.level');
    const levelStyle = getComputedStyle(cell);
    measure.font = levelStyle.fontSize + ' ' + levelStyle.fontFamily;
    const widest = Math.max(...[...ladder.querySelectorAll('.level')].map(n=>measure.measureText(n.textContent).width));
    const pane = getComputedStyle(sheet.querySelector('.lyric-page'));
    const inset = parseFloat(pane.paddingLeft) + parseFloat(pane.paddingRight);
    minSkills = Math.max(minSkills, 286, Math.ceil(inset + 58 + 5 * (widest + 5)));
  }
  for (let art = minCover; art <= (related ? 282 : 248); art += 4) {
    for (let extras = related ? 202 : 0; extras <= (related ? 250 : 0); extras += 4) {
      const skills = width - art - extras;
      if (skills < minSkills) continue;
      const columns = (related ? [art,skills,extras] : [art,skills]).map(n=>n+'px').join(' ');
      sheet.style.gridTemplateColumns = columns;
      const heights = panels.map(panel => panel.getBoundingClientRect().height);
      const tallest = Math.max(...heights);
      const overflow = panels.some(panel=>panel.scrollWidth > panel.clientWidth + 1);
      if (overflow || (relatedList && relatedList.scrollWidth > relatedList.clientWidth + 1)) continue;
      // A wide percentage can fit the fold while spilling into the paper's inner margin.
      if ([...eventNotes].some(note => note.scrollWidth > note.clientWidth)) continue;
      const widths = related ? [art,skills,extras] : [art,skills];
      const unused = heights.reduce((area,h,i)=>area + (tallest-h)*widths[i]/width, 0);
      // A taller insert can afford a larger cover in the same three folds.
      const coverTarget = related ? 248 + Math.min(32, Math.max(0, tallest-heights[0]-30)/4) : 228;
      const portraitWeight = related && tallest-heights[0]>118 ? 1.15 : .40;
      const score = tallest + .18*unused + (related ? .28 : .45)*Math.abs(skills-preferredSkillWidth)
        + portraitWeight*Math.abs(art-coverTarget) + (related ? .07*Math.abs(extras-216) : 0);
      if (!best || score < best.score) best = {score,columns};
    }
  }
  if (best) sheet.style.gridTemplateColumns = best.columns;
  else sheet.style.removeProperty('grid-template-columns');
  const stats = sheet.querySelector('.stats');
  const before = stats.getBoundingClientRect().height;
  const artHeight = panels[0].getBoundingClientRect().height;
  const foldHeight = Math.max(...panels.map(panel => panel.getBoundingClientRect().height));
  const spare = foldHeight - artHeight;
  const room = Math.min(1, Math.max(0, (spare-12)/122));
  sheet.style.setProperty('--stat-room',room);
  // A six-digit total can fill its column before the vertical space runs out.
  const tableFrame = stats.parentElement;
  for(let step=0;step<=10;step++){
    sheet.style.setProperty('--stat-type-room', room*(1-step/10));
    if(tableFrame.scrollWidth<=tableFrame.clientWidth) break;
  }
  sheet.classList.remove('measuring');
  if (relatedList) {
    const foot = relatedList.parentElement.getBoundingClientRect().bottom - relatedList.getBoundingClientRect().bottom;
    sheet.style.setProperty('--imprint-height', Math.max(0, Math.min(100, foot - 10)) + 'px');
  }
  return {before, after:stats.getBoundingClientRect().height, foldHeight, spare};
}

window.foldReady = fitFolds();

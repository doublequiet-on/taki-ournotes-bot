import {html, render} from 'lit';
import {cardTile} from 'pinned/shared/card-tile';
import {tileMedia} from 'pinned/ui/tile';
import {LazyImages} from 'pinned/ui/lazy-images';

const {cards, people, command} = window.listInput;
const attributes = {1: ['Red', '红色'], 2: ['Blue', '蓝色'], 3: ['Green', '绿色'],
  4: ['Yellow', '黄色'], 5: ['Purple', '紫色']};
const media = name => '/media/' + encodeURIComponent(name);

function badge(icon, label, value) {
  return html`<span class="skill"><img src=${media(icon)} alt=${label}>
    <span class="number"><b>${value}</b></span></span>`;
}

function card(id) {
  const entry = cards[id];
  const [attribute, label] = attributes[entry.color];
  const tile = cardTile({kind: 'member', title: '', subtitle: '', label: '成员卡 ID ' + id,
    image: media(entry.art), attributeIcon: media('attr-' + attribute + '.png'),
    attributeLabel: label, rarityIcon: '', rarityLabel: ''});
  return html`<article data-card=${id}>
    <div class="art" style=${'--frame:url(' + media('frame-' + entry.rarity + '.png') + ')'}>
      ${tileMedia(tile)}
      <div class="skill-strip">
        ${badge(entry.leaderIcon, '队长技能', entry.leader + '%')}
        ${badge(entry.scoreIcon, '演出技能', entry.score + '%')}
        ${badge(entry.gekIcon, '激奏技能', entry.gek)}
      </div>
    </div>
    <div class="copy"><strong>ID ${id}</strong></div>
  </article>`;
}

function person(entry) {
  const names = entry.name.split(' / ');
  return html`<section style=${'--accent:' + entry.color} aria-label=${entry.name}>
    <div class="who"><img src=${media(entry.face)} alt=""><b>${names.map((name, i) =>
      html`<span class=${i < names.length - 1 ? 'stage-name' : 'name-part'}>${name}</span>`)}</b></div>
    <div class="cards">${entry.ids.map(card)}</div>
  </section>`;
}

// Give each of the three values the same column across every card in this image.
function alignBadges(sheet) {
  const bars = [...sheet.querySelectorAll('.skill-strip')];
  if (!bars.length) return;
  const rows = bars.map(bar => [...bar.querySelectorAll('.number b')]);
  const widths = rows.map(row => row.map(label => parseFloat(getComputedStyle(label).width)));
  const preferred = widths[0].map((_, i) => Math.max(18, ...widths.map(row => row[i])));
  const total = preferred.reduce((sum, n) => sum + n, 0);
  const room = bars[0].getBoundingClientRect().width - 8;
  const factor = Math.min(1, room / total);
  const extra = (room - factor * total) / 3;
  const available = preferred.map(width => width * factor + extra);
  const tracks = available.map((width, i) => (width + 2 + Number(i > 0)) + 'px').join(' ');
  bars.forEach((bar, i) => {
    bar.style.gridTemplateColumns = tracks;
    rows[i].forEach((label, col) =>
      label.style.setProperty('--fit', String(Math.min(1, available[col] / widths[i][col]))));
  });
}

render(html`<main id="sheet" aria-label=${command}>${people.map(person)}</main>`, document.querySelector('#root'));
const loader = new LazyImages();
for (const image of document.querySelectorAll('img[data-src]')) loader.load(image);
window.listReady = (async () => {
  await Promise.all([...document.images].map(image => image.decode()));
  await document.fonts.ready;
  await document.fonts.load('600 16px "Roboto Variable"', '0123456789+%pt');
  alignBadges(document.querySelector('#sheet'));
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
})();

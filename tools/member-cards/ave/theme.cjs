const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const applyFacts = require('./apply-facts.cjs');
const drawSeatingPlan = require('./balcony-gains.cjs');

module.exports = async function arrangeProgramme(page, facts) {
    await page.addStyleTag({url:'/ave/programme.css'});
    await page.addStyleTag({url:'/ave/live-booklet.css'});
    await page.evaluate(applyFacts, facts);
    await page.addStyleTag({url: '/ave/balcony-gains.css'});
    await page.addStyleTag({url:'/ave/dark-stage.css'});
    await page.addStyleTag({url:'/ave/programme-relations.css'});
    await page.addStyleTag({url:'/ave/paper-theatre.css'});
    await page.evaluate(async () => {
      await Promise.all([...document.fonts].filter(face => face.family.includes('Ave ')).map(face => face.load()));
    });
    await page.evaluate(() => {
      for (const entry of document.querySelectorAll('main[data-band-id="2"] .events dd')) {
        const caption = entry.querySelector('.relation-caption');
        const masthead = document.createElement('div');
        masthead.className = 'poster-masthead';
        const category = document.createElement('span');
        category.textContent = entry.parentElement.querySelector('dt').textContent;
        masthead.append(category, caption.querySelector('.relation-id'));
        const name = caption.querySelector('.relation-name');
        const tag = [...name.querySelectorAll('.keep')].find(part => part.textContent.startsWith('#'));
        if (tag && tag.previousSibling && tag === name.lastChild) {
          tag.classList.add('poster-tag');
          name.classList.add('has-poster-tag');
        }
        caption.prepend(masthead);
        entry.prepend(caption);
      }
      for (const stub of document.querySelectorAll('main[data-band-id="2"] .gachas .relation-id')) {
        const [label, number] = stub.textContent.trim().split(/\s+/);
        const serial = document.createElement('b');
        serial.textContent = number;
        stub.replaceChildren(label + ' ', serial);
      }
    });
    await page.evaluate(drawSeatingPlan, facts);
    await page.evaluate(({art,id}) => {
      const header=document.querySelector('.lyric-page header');
      const dock=document.createElement('div');
      dock.className='ave-member-dock';
      const identity=document.createElement('div');
      const member=header.querySelector('.member-line'), traits=header.querySelector('.traits');
      const name=member.querySelector('strong');
      const [alias,person]=name.textContent.split(/\s*\/\s*/);
      if (person) {
        const stageName=document.createElement('span');
        stageName.className='ave-stage-name';
        stageName.textContent=alias;
        name.before(stageName);
        name.textContent=person;
      }
      member.before(dock);
      identity.append(member,traits);
      const parsed=new DOMParser().parseFromString(art,'image/svg+xml');
      const instruments=document.importNode(parsed.documentElement,true);
      instruments.classList.add('ave-member-instruments');
      const active=instruments.querySelectorAll('[data-character-id="'+id+'"]');
      if (active.length!==1) throw Error('Unknown Ave Mujica member '+id);
      active[0].classList.add('current-instrument');
      instruments.setAttribute('role','img');
      instruments.setAttribute('aria-label',name.textContent.trim()+' 的乐器已高亮：'+active[0].dataset.gear);
      dock.append(identity,instruments);
    },{art:readFileSync(join(__dirname,'member-instruments.svg'),'utf8'),id:facts.characterId});
    await page.evaluate(({art, figures}) => {
      const rig = document.createElement('div');
      rig.className = 'ave-puppet-scene';
      rig.setAttribute('aria-hidden', 'true');
      const parser = new DOMParser();
      const cabinet = parser.parseFromString(art, 'image/svg+xml').documentElement;
      const puppets = parser.parseFromString(figures, 'image/svg+xml').documentElement;
      const defs = cabinet.querySelector('defs');
      for (const part of puppets.querySelectorAll(':scope > defs > *')) {
        defs.appendChild(part);
      }
      // Share real instrument artwork with the header, without its placement or active-member treatment.
      const lineup = document.querySelector('.ave-member-instruments');
      for (const host of cabinet.querySelectorAll('[data-room-member]')) {
        const member = lineup.querySelector('[data-character-id="' + host.dataset.roomMember + '"]');
        const source = host.dataset.sourcePart ? member.querySelector('#' + host.dataset.sourcePart) : member;
        host.prepend(...Array.from(source.children, part => part.cloneNode(true)));
        for (const shape of host.querySelectorAll('[fill^="url(#"]')) {
          const id = shape.getAttribute('fill').slice(5, -1);
          if (defs.querySelector('#' + id)) continue;
          const localId = 'room-' + id;
          if (!defs.querySelector('#' + localId)) {
            const paint = lineup.querySelector('defs #' + id).cloneNode(true);
            paint.id = localId;
            defs.appendChild(paint);
          }
          shape.setAttribute('fill', 'url(#' + localId + ')');
        }
      }
      rig.append(document.importNode(cabinet, true));
      document.querySelector('.art-page .numbers').after(rig);
    }, {
      art: readFileSync(join(__dirname, 'puppet-cabinet.svg'), 'utf8'),
      figures: readFileSync(join(__dirname, 'seated-puppets.svg'), 'utf8'),
    });
    await page.evaluate(() => fitFolds());
    await page.evaluate(() => {
      const sheet = document.querySelector('main[data-band-id="2"]');
      if (!sheet) throw new Error('Expected an Ave Mujica detail sheet');
      if ([...document.images].some(image => !image.complete || !image.naturalWidth)) throw new Error('Missing game image');
      for (const entry of sheet.querySelectorAll('.relations dd')) {
        if (entry.querySelectorAll('img.relation-banner').length !== 1)
          throw Error('Expected one original banner for ' + entry.querySelector('.relation-caption').textContent);
      }
      function layer(tag, classes, parent) {
        const el = document.createElement(tag);
        el.className = classes;
        parent.append(el);
        return el;
      }
      const stage = layer('div', 'ave-stage', sheet.parentNode);
      layer('div', 'ave-apron', stage).setAttribute('aria-hidden','true');
      const book = layer('div','ave-book',stage);
      const print = layer('div','ave-print',book);
      print.setAttribute('aria-hidden','true');
      book.append(sheet);
      layer('i', 'ave-border', print);
      for (const corner of ['nw','ne','sw','se']) layer('i','ave-corner '+corner,print);
      const panels = [...sheet.querySelectorAll(':scope > section')];
      // Under the jacket's final typography the five bonus readings may need
      // more room than the original fold search allows. Widen only the insert.
      const bonusTables = [...sheet.querySelectorAll('.events .bonus-table')];
      const posterInnerRight = table => {
        const poster = table.closest('dd');
        const style = getComputedStyle(poster);
        return poster.getBoundingClientRect().right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth);
      };
      const extra = Math.ceil(Math.max(0, ...bonusTables.map(table =>
        table.getBoundingClientRect().right - posterInnerRight(table))));
      if (extra) {
        const sizes = panels.map(panel => panel.getBoundingClientRect().width);
        sheet.style.width = (sheet.getBoundingClientRect().width + extra) + 'px';
        sizes[sizes.length - 1] += extra;
        sheet.style.gridTemplateColumns = sizes.map(width => width + 'px').join(' ');
      }
      for (const table of bonusTables) {
        const range = document.createRange();
        for (const cell of table.querySelectorAll('th,td')) {
          range.selectNodeContents(cell);
          if (range.getBoundingClientRect().right > posterInnerRight(table) + 1)
            throw Error('Bonus reading outside poster: ' + cell.textContent);
        }
      }
      const x0 = print.getBoundingClientRect().left;
      for (const [i,panel] of panels.slice(1).entries()) {
        const x = panel.getBoundingClientRect().left - x0;
        layer('i', 'ave-fold' + (i ? ' ridge' : ''), print).style.left = x + 'px';
      }
      const middle = panels[1].getBoundingClientRect();
      layer('i', 'ave-mark', print).style.left = middle.left + middle.width/2 - x0 + 'px';
      return {columns: panels.map(p => Math.round(p.getBoundingClientRect().width)), size: [stage.offsetWidth,stage.offsetHeight]};
    });
    return page.locator('.ave-stage');
};

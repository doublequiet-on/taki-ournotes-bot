const fs = require('node:fs');
const path = require('node:path');
const faderBank = require('./fader-bank.cjs');
const masterAxis = require('./master-axis.cjs');
const installDemo = require('./slider-demo.cjs');

module.exports = async function arrangeDesktop(page, data) {
    const growth = data.cultivation;
    for (const file of ['console.css','paper-faces.css','paper-palette.css','logo-inks.css']) {
      await page.addStyleTag({url:'/mewtype/'+file});
    }
    const snack = page.locator('.art-page .snack-corner img');
    await snack.evaluate(async (img, url) => {
      img.src = url;
      img.alt = '竹皮包着厚方羊羹，白纸签上并排写着巨羊二字，深色端面边缘留有一处浅缺';
      await img.decode();
    }, new URL('/mewtype/left-yokan-still-giant.svg',page.url()).href);
    const channels=growth.stats.map((s,i)=>({
      ...s, label:data.stats[i].label, color:data.stats[i].color, levels:data.levelCurve[s.key]
    }));
    await page.evaluate(async ({drawing,master,sticker})=>{
      const numbers=document.querySelector('.art-page .numbers');
      const unit=document.createElement('div');unit.className='console-unit';
      const lid=document.createElement('div');lid.className='console-bridge';
      lid.innerHTML='<i class="fastener"></i>'+sticker+'<i class="grille"></i><i class="status"></i><i class="fastener"></i>';
      const bank=document.createElement('div');bank.className='console-bank';bank.innerHTML=drawing;
      const legend=document.createElement('div');legend.className='level-key';
      legend.innerHTML='<span><i class="tick-key"></i>计入特训</span>';
      lid.querySelector('.grille').before(legend);
      unit.append(lid,bank);
      document.querySelector('.art-page .cultivation')?.remove();
      numbers.replaceWith(unit);
      unit.insertAdjacentHTML('beforeend',master);
      await document.fonts.ready;
      await document.fonts.load('34px "ZCOOL KuaiLe"', '属性值');
    }, {drawing:faderBank(channels),master:masterAxis(channels,growth),sticker:fs.readFileSync(path.join(__dirname,'stat-sticker.svg'),'utf8')});
    await page.evaluate(installDemo,channels);
    await page.addStyleTag({url:'/mewtype/preview.css'});
    await page.evaluate(require('./preview.cjs'));
    await page.addStyleTag({url:'/mewtype/studio.css'});
    await page.evaluate(require('./studio.cjs'));
    await page.addStyleTag({url:'/mewtype/filters.css'});
    await page.addStyleTag({url:'/mewtype/trough.css'});
    await page.addStyleTag({url:'/mewtype/splitter.css'});
    await page.addStyleTag({url:'/mewtype/pink-window.css'});
    await page.addStyleTag({url:'/mewtype/relation-window.css'});
    await page.evaluate(require('./relation-window.cjs'), String(data.id));
    await page.addStyleTag({url:'/mewtype/gacha-ad.css'});
    await page.evaluate(require('./gacha-ad.cjs'));
    await page.addStyleTag({url:'/mewtype/typography.css'});
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(require('./filters.cjs'), {skills:data.skills, liveNotes:data.liveNotes || {}});
    await page.addStyleTag({url:'/mewtype/ensemble.css'});
    await page.evaluate(require('./ensemble.cjs'),new URL('/mewtype/',page.url()).href);
    await page.evaluate(require('./desktop.cjs'), {
      shelfURL:new URL('/mewtype/shelf.svg',page.url()).href,
      maruArtwork:fs.readFileSync(path.join(__dirname,'maru-edge.svg'),'utf8')
    });
    await page.addStyleTag({url:new URL('/mewtype/desktop.css',page.url()).href});
    await page.addStyleTag({url:new URL('/mewtype/studio-depth.css',page.url()).href});
    await page.addStyleTag({url:new URL('/mewtype/popup-depth.css',page.url()).href});
    await page.evaluate(require('./mixer-depth.cjs'),channels);
    await page.addStyleTag({url:new URL('/mewtype/mixer-depth.css',page.url()).href});
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.evaluate(async () => {
      await document.fonts.ready;
      document.querySelector(".obs-preview").fitTitle();
    });
    return page.locator('#print');
};

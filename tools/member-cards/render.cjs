const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright-core');
const arrangeAve = require('./ave/theme.cjs');
const arrangeMew = require('./mewtype/theme.cjs');

const types = {'.css':'text/css', '.js':'text/javascript', '.svg':'image/svg+xml',
  '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp',
  '.ttf':'font/ttf', '.woff2':'font/woff2'};

// Fulfill the virtual document and assets from disk. No listener or internet connection.
const origin = 'http://preview.invalid';
function serve(route, html, mediaDirectory) {
  const url = new URL(route.request().url());
  if (url.origin !== origin) return route.abort();
  if (url.pathname === '/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
  const roots = {base:'base',ave:'ave',mewtype:'mewtype',media:mediaDirectory,
    fontsource:'node_modules/@fontsource-variable'};
  let file;
  if (url.pathname === '/fonts.css') file = path.join(__dirname,'fonts.css');
  else {
    const [,group,...parts] = url.pathname.split('/');
    if (Object.hasOwn(roots,group) && parts.every(part => /^[a-zA-Z0-9_.-]+$/.test(part) && part !== '..')) {
      file = path.resolve(__dirname,roots[group],...parts);
    }
  }
  const type = file && types[path.extname(file)];
  if (!type || !fs.statSync(file,{throwIfNoEntry:false})?.isFile()) return route.fulfill({status:404,body:''});
  return route.fulfill({path:file,contentType:type});
}

async function render(input, output, mediaDirectory = path.join(__dirname,'.cache')) {
  const card = JSON.parse(fs.readFileSync(input,'utf8'));
  const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const document = JSON.parse(execFileSync(python, ['-B',path.join(__dirname,'sheet.py'),path.resolve(input)],
    {encoding:'utf8',maxBuffer:8*1024*1024}));
  let browser;
  try {
    browser = await chromium.launch({executablePath:process.env.BROWSER_PATH || undefined,headless:true});
    const viewport = card.bandId === 2 ? {width:1200,height:1240} : {width:1120,height:1050};
    const page = await browser.newPage({viewport,deviceScaleFactor:3,locale:'zh-CN'});
    const missing = [];
    await page.route('**/*',route => serve(route,document.html,path.resolve(mediaDirectory)));
    page.on('response',r=>{if(r.status()>=400 && !r.url().endsWith('/favicon.ico'))missing.push(r.url());});
    await page.goto(origin+'/');
    await page.evaluate(()=>window.foldReady);
    let target = page.locator('main');
    if (card.bandId === 2) {
      target = await arrangeAve(page,{characterId:card.characterId, growth:card.cultivation,
        articles:document.articles,labels:card.stats.map(s=>s.label),debut:card.jpDebutDate});
    } else if (card.bandId === 3) {
      target = await arrangeMew(page,card);
    }
    await page.evaluate(async () => {
      await Promise.all([...document.images].map(i=>i.decode()));
      await document.fonts.ready;
      const entries = document.querySelectorAll('.relations dd');
      for (const entry of entries) {
        if (!entry.querySelector('.relation-banner')) throw Error('Missing banner: '+entry.textContent);
      }
    });
    if (missing.length) throw Error('Missing preview resources: '+missing.join(', '));
    const count = card.relations.events.length + card.relations.gachas.length;
    const panels = await page.locator('main > section').count();
    if (panels !== (count ? 3 : 2)) throw Error('Wrong fold count');
    const png = await target.screenshot({animations:'disabled'});
    fs.mkdirSync(path.dirname(path.resolve(output)),{recursive:true});
    fs.writeFileSync(output,png);
    console.log('Card',card.id,'·',panels,'folds ·',png.readUInt32BE(16),'×',png.readUInt32BE(20),output);
  } finally {
    await browser?.close();
  }
}

if (require.main === module) {
  const [input,output,media] = process.argv.slice(2);
  if (!input || !output) throw Error('Usage: node render.cjs CARD.json OUTPUT.png [MEDIA_DIRECTORY]');
  render(input,output,media).catch(error=>{console.error(error);process.exitCode=1;});
}
module.exports = render;

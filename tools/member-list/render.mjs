import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium} from 'playwright-core';

const root = path.dirname(fileURLToPath(import.meta.url));
const origin = 'http://preview.invalid';
const [key, output, input = path.join(root, 'examples/lists.json')] = process.argv.slice(2);
if (!key || !output || path.extname(output).toLowerCase() !== '.png') {
  throw Error('Usage: node render.mjs CASE OUTPUT.png [LISTS.json]');
}
const {cases, cards} = JSON.parse(await readFile(input, 'utf8'));
if (!Object.hasOwn(cases, key)) throw Error('Available lists: ' + Object.keys(cases).join(', '));
const sample = cases[key];
const ids = sample.people.flatMap(person => person.ids);
if (!ids.length || sample.people.some(person => !person.ids.length)) throw Error('Expected card rows.');
if (new Set(ids).size !== ids.length) throw Error('A member card occurs more than once.');
for (const id of ids) {
  if (cards[id]?.id !== id) throw Error('Missing member card: ' + id);
}
const count = Math.max(...sample.people.map(person => person.ids.length));
const width = Math.max(320, 108 + count * 117 + (count - 1) * 4);
const source = path.join(root, '.cache/haneoka');
const bundle = await build({
  absWorkingDir: root, entryPoints: ['list.js'], outdir: 'out', write: false,
  bundle: true, format: 'iife', target: 'chrome130',
  alias: {pinned: path.join(source, 'src/lit'), '@haneoka/i18n': path.join(source, 'packages/i18n/src/index.ts')},
  define: {'import.meta.env.SSR': 'false'},
});
const script = bundle.outputFiles.find(file => file.path.endsWith('.js')).text;
const componentCss = bundle.outputFiles.find(file => file.path.endsWith('.css'))?.text || '';
const sheetCss = await readFile(path.join(root, 'list.css'), 'utf8');
const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
  <link rel="icon" href="data:,"><link rel="stylesheet" href="/fonts/roboto/wght.css">
  <link rel="stylesheet" href="/fonts/noto-sans-sc/wght.css">
  <style>${componentCss}\n${sheetCss}\n:root { --sheet-width: ${width}px; }</style>
  <div id="root"></div></html>`;

function resource(url) {
  if (url === '/icons.svg') return [path.join(source, 'public/icons.svg'), 'image/svg+xml'];
  if (/^\/media\/[\w.-]+\.png$/.test(url)) {
    return [path.join(root, '.cache', url.slice(1)), 'image/png'];
  }
  const font = /^\/fonts\/(roboto|noto-sans-sc)\/(wght\.css|files\/[\w.-]+\.woff2?)$/.exec(url);
  if (font) {
    return [path.join(root, 'node_modules/@fontsource-variable', font[1], font[2]),
      font[2].endsWith('.css') ? 'text/css' : 'font/woff2'];
  }
}

const browser = await chromium.launch({executablePath: process.env.BROWSER_PATH || undefined});
try {
  const page = await browser.newPage({viewport: {width, height: 900}, deviceScaleFactor: 3, locale: 'zh-CN'});
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === origin && url.pathname === '/') {
      return route.fulfill({contentType: 'text/html; charset=utf-8', body: html});
    }
    const local = url.origin === origin && resource(url.pathname);
    if (local) {
      try {
        return await route.fulfill({body: await readFile(local[0]), contentType: local[1]});
      } catch (error) {
        failures.push(error.message);
      }
    }
    failures.push(url.href);
    await route.fulfill({status: 404, body: ''});
  });
  await page.addInitScript(data => { window.listInput = data; }, {...sample, cards});
  await page.goto(origin);
  await page.addScriptTag({content: script});
  await page.evaluate(async () => {
    if (!window.listReady) throw Error('Member list did not initialize.');
    await window.listReady;
  });
  if (failures.length) throw Error(failures.join('\n'));
  await mkdir(path.dirname(path.resolve(output)), {recursive: true});
  await page.locator('#sheet').screenshot({path: output, animations: 'disabled'});
  console.log(`${key}: ${sample.people.length} members, ${ids.length} cards → ${output}`);
} finally {
  await browser.close();
}

// Input: locked webui source and supplied upstream source trees in cwd.
// Output: downstream lib/dist plus module generation evidence; no service startup.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = process.cwd();
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const ep = path.join(root, 'taki-sources/element-plus');
const report = path.join(root, 'taki-evidence');
fs.mkdirSync(report, { recursive: true });
const devtoolsEntry = path.join(root, 'taki-sources/devtools/packages/api/src/index.ts');
const envPath = path.join(path.dirname(devtoolsEntry), 'env.ts');
const envBefore = fs.readFileSync(envPath, 'utf8');
let envAfter = envBefore;
for (const [from, to] of [
  ['  // @ts-expect-error navigator and windows are not available in all environments\n', ''],
  ['? window\n', '? (window as Window & GlobalTarget)\n'],
  ['? globalThis\n', '? (globalThis as typeof globalThis & GlobalTarget)\n'],
]) {
  if (envAfter.split(from).length !== 2) throw Error('Unexpected devtools environment source');
  envAfter = envAfter.replace(from, to);
}
fs.writeFileSync(envPath, envAfter);
fs.writeFileSync(path.join(report, 'devtools-type-patch.json'), JSON.stringify({
  path: 'packages/api/src/env.ts', before: hash(envBefore), after: hash(envAfter),
  purpose: 'Replace obsolete expect-error with explicit optional devtools global properties; runtime expressions unchanged',
}, null, 2));
// Use the upstream whole-program compiler semantics: erase type re-exports and
// const enums. Isolated Vite transpilation would expose the Hooks const enum.
const ts = require('typescript');
const devtoolsConfigPath = path.join(root, 'taki-sources/devtools/packages/api/tsconfig.json');
const devtoolsConfig = ts.readConfigFile(devtoolsConfigPath, ts.sys.readFile);
if (devtoolsConfig.error) throw Error(ts.flattenDiagnosticMessageText(devtoolsConfig.error.messageText, '\n'));
const devtoolsParsed = ts.parseJsonConfigFileContent(devtoolsConfig.config, ts.sys, path.dirname(devtoolsConfigPath), {
  outDir: path.join(root, 'taki-generated/devtools'), module: ts.ModuleKind.ES2015,
  // This browser API uses Node ambient names but no webpack ambient names.
  // Check every source file against the locked Node types; do not suppress diagnostics.
  types: ['node'], noEmitOnError: true, declaration: true, sourceMap: true,
});
const devtoolsProgram = ts.createProgram(devtoolsParsed.fileNames, devtoolsParsed.options);
const diagnostics = [...devtoolsParsed.errors, ...ts.getPreEmitDiagnostics(devtoolsProgram)];
if (diagnostics.length) throw Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
  getCanonicalFileName: x => x, getCurrentDirectory: () => root, getNewLine: () => '\n',
}));
if (devtoolsProgram.emit().emitSkipped) throw Error('Devtools emit skipped');
fs.writeFileSync(path.join(report, 'generate-devtools.json'), JSON.stringify({
  sourceVersion: '6.6.3', downstream: '6.6.3-taki.1', compiler: ts.version,
  config: devtoolsConfig.config, overrides: { module: 'ES2015', types: ['node'], noEmitOnError: true, declaration: true, sourceMap: true },
  inputs: Object.fromEntries(devtoolsParsed.fileNames.map(p => [p.replace(root + '/', ''), hash(fs.readFileSync(p))])),
}, null, 2));
const lodashExports = Object.keys(require('lodash-es')).filter(x => !['default', '__esModule'].includes(x)).sort();
const lodashAdapter = path.join(root, 'taki-generated/lodash-browser.mjs');
fs.mkdirSync(path.dirname(lodashAdapter), { recursive: true });
// New Taki adapter generated from the locked lodash-es public namespace, not
// copied from lodash-unified. Preserve export-star's absence of a default export.
fs.writeFileSync(lodashAdapter, '// Copyright (c) 2026 Taki contributors. MIT.\nexport { ' + lodashExports.join(', ') + " } from 'lodash-es';\n");
fs.writeFileSync(path.join(report, 'lodash-adapter.json'), JSON.stringify({
  source: 'locked lodash-es public namespace', exports: lodashExports, sha256: hash(fs.readFileSync(lodashAdapter)),
  copied_from_lodash_unified: false, license: 'MIT (Taki adapter only)',
}, null, 2));
const aliases = {
  'element-plus/dist/index.css': ep + '/packages/theme-chalk/src/index.scss',
  'element-plus': ep + '/packages/element-plus/index.ts',
  'lodash-unified': lodashAdapter,
  '@vue/devtools-api': path.join(root, 'taki-generated/devtools/index.js'),
};
for (const name of ['components', 'constants', 'directives', 'hooks', 'utils', 'locale', 'tokens']) {
  aliases['@element-plus/' + name] = ep + '/packages/' + name;
}
for (const name of ['@cordisjs/core', '@cordisjs/logger', '@cordisjs/schema', '@cordisjs/timer',
  'cordis', '@satorijs/element', '@satorijs/protocol', 'schemastery']) {
  const outfile = path.join(root, 'taki-generated', name.replaceAll('/', '_') + '.mjs');
  const result = require('esbuild').buildSync({
    absWorkingDir: root, entryPoints: ['node_modules/' + name + '/src/index.ts'],
    outfile, bundle: true, packages: 'external', format: 'esm', platform: 'browser',
    sourcemap: true, metafile: true, legalComments: 'eof',
  });
  fs.writeFileSync(path.join(report, 'generate-' + name.replaceAll('/', '_') + '.json'), JSON.stringify(result.metafile, null, 2));
  aliases[name] = outfile;
}
// Security patch applies to preferred TS before compilation, never minified JS.
const markdownSource = path.join(root, 'taki-sources/marked-vue/src/index.ts');
const markdownBefore = hash(fs.readFileSync(markdownSource));
if (markdownBefore !== '810c43dcad4749eb6e1c00670d13f5929aac2252ba77f05b1cddc1307d8964a6') throw Error('Unexpected marked-vue source; refuse repeated security patch');
require('node:child_process').execFileSync('git', ['-c', 'core.autocrlf=false', 'apply', '--unidiff-zero',
  path.join(root, '../recipe/marked-vue-href.patch')], { cwd: root });
const markdownAfter = hash(fs.readFileSync(markdownSource));
if (markdownAfter !== '19e78466c78037a5a18e1dedc64f6420e90f895f587b6651e4b09e59e3104930') throw Error('Unexpected marked-vue security patch result');
fs.writeFileSync(path.join(report, 'marked-vue-security-patch.json'), JSON.stringify({
  path: 'taki-sources/marked-vue/src/index.ts', before: markdownBefore, after: markdownAfter,
  patch_sha256: hash(fs.readFileSync(path.join(root, '../recipe/marked-vue-href.patch'))),
  purpose: 'HTML attribute decode once, unchanged URL protocol allowlist, then attribute encoding',
}, null, 2));
// Preferred-source browser modules; dependencies remain explicit externals at
// this stage and are resolved by the locked Vite graph in the final chunks.
for (const [name, entry, externals] of [
  ['marked-vue', 'taki-sources/marked-vue/src/index.ts', ['vue', 'marked', 'xss']],
  ['reggol', 'node_modules/reggol/src/browser.ts', ['cosmokit', 'supports-color', 'object-inspect']],
]) {
  if (name === 'reggol' && JSON.parse(fs.readFileSync('node_modules/reggol/package.json')).version !== '1.7.1') {
    throw Error('Expected exact npm reggol 1.7.1 sources');
  }
  const outfile = path.join(root, 'taki-generated', name + '.mjs');
  const options = {
    absWorkingDir: root, entryPoints: [entry], outfile, bundle: true,
    external: externals, format: 'esm', platform: 'browser', target: 'es2022',
    keepNames: true, sourcemap: true, sourcesContent: true, metafile: true, legalComments: 'eof',
  };
  const result = require('esbuild').buildSync(options);
  fs.writeFileSync(path.join(report, 'generate-' + name + '.json'), JSON.stringify({
    generator: 'esbuild', version: require('esbuild').version, options,
    type_modifications: [], name_policy: 'keepNames; no minification at source generation',
    preferred_source: name === 'reggol' ? 'exact npm 1.7.1 bundled TS' : '86f4fa602bc4ad588661bbddcc0fee5126c295b7 (tree package version 1.2.3)',
    inputs: Object.fromEntries(Object.keys(result.metafile.inputs).map(p => [p, hash(fs.readFileSync(p))])),
    metafile: result.metafile,
  }, null, 2));
  aliases[name] = outfile;
}
fs.writeFileSync(ep + '/packages/element-plus/version.ts', "export const version = '2.7.7-taki.1'\n");
const recipe = path.join(root, 'packages/client/scripts/client.ts');
let script = fs.readFileSync(recipe, 'utf8');
function replaceOnce(old, value) {
  if (script.split(old).length !== 2) throw Error('Unexpected build recipe: ' + old);
  script = script.replace(old, value);
}
replaceOnce("import vue from '@vitejs/plugin-vue'", "import vue from '@vitejs/plugin-vue'\nimport vueJsx from '@vitejs/plugin-vue-jsx'\nconst taki = require('../../../taki-build.cjs')");
replaceOnce('      vue(),', '      vue(),\n      vueJsx(),\n      taki.trace(root),');
replaceOnce('      alias: {', '      alias: {\n        ...taki.aliases,');
fs.writeFileSync(recipe, script);
let counter = 0;
module.exports.aliases = aliases;
module.exports.trace = function(buildRoot) {
  const modules = {};
  return {
    name: 'taki-source-evidence',
    transform(code, id) {
      const key = id.replaceAll(root + '/', '');
      modules[key] = { transformed_input_sha256: hash(code), bytes: Buffer.byteLength(code) };
      if (id.includes('plugin-vue:export-helper')) modules[key].generated_source = code;
      const file = id.split('?')[0];
      if (file.startsWith(root + '/') && fs.existsSync(file) && fs.statSync(file).isFile()) {
        modules[key].file_sha256 = hash(fs.readFileSync(file));
      }
      return null;
    },
    generateBundle(options, bundle) {
      const outputs = {};
      for (const [name, item] of Object.entries(bundle)) {
        outputs[name] = item.type === 'chunk'
          ? { modules: Object.keys(item.modules).map(x => x.replaceAll(root + '/', '')), exports: item.exports, imports: item.imports }
          : { asset: true };
      }
      fs.writeFileSync(path.join(report, 'graph-' + (++counter) + '.json'), JSON.stringify({ root: buildRoot.replace(root + '/', ''), modules, outputs }, null, 2));
    },
  };
};
if (require.main === module) {
  require('tsx/cjs');
  require('./packages/client/scripts/client.ts').default().catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}

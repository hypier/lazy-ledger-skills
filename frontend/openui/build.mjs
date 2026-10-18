import { build } from 'esbuild';
import { execFileSync } from 'node:child_process';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(root, '../../assets/openui');
await build({ absWorkingDir: root, entryPoints: ['src/main.jsx'], outfile: `${out}/renderer.js`,
  bundle: true, minify: true, format: 'iife', platform: 'browser', target: ['es2020'],
  define: { 'process.env.NODE_ENV': '"production"', 'process.env.OPENUI_TELEMETRY_DISABLED': '"1"', 'process.env.DO_NOT_TRACK': '"1"' },
  legalComments: 'inline', charset: 'utf8' });
// A script element is the distribution container, so preserve its HTML boundary.
const bundle = (await readFile(`${out}/renderer.js`, 'utf8')).replace(/<\/script/gi, '<\\/script');
await writeFile(`${out}/renderer.js`, bundle);
await writeFile(`${out}/renderer.css`, await readFile(`${root}/src/style.css`));
const lock = JSON.parse(await readFile(`${root}/package-lock.json`));
const dependencies = {};
const licenses = [];
for (const [dir, info] of Object.entries(lock.packages)) {
  if (!dir || info.dev || info.optional) continue;
  dependencies[dir.replace(/^node_modules\//, '')] = info.version;
  for (const name of (await readdir(`${root}/${dir}`)).filter(n => /^(license|copying)(\.|$)/i.test(n))) {
    licenses.push(`${dir} / ${name}\n${await readFile(`${root}/${dir}/${name}`, 'utf8')}`);
  }
}
await writeFile(`${out}/THIRD_PARTY_LICENSES.txt`, licenses.join('\n\n'));
await build({ absWorkingDir: root, entryPoints: ['src/generation-cli.jsx'], outfile: `${out}/validate.cjs`,
  bundle: true, minify: true, format: 'cjs', platform: 'node', target: ['node18'],
  define: { 'process.env.NODE_ENV': '"production"', 'process.env.OPENUI_TELEMETRY_DISABLED': '"1"', 'process.env.DO_NOT_TRACK': '"1"' } });
const spec = JSON.parse(execFileSync(process.execPath, [`${out}/validate.cjs`, '--spec'], { encoding: 'utf8' }));
await writeFile(`${out}/generation.json`, JSON.stringify(spec, null, 2) + '\n');
const sha256 = {};
for (const name of ['renderer.js', 'renderer.css', 'catalog.json', 'validate.cjs', 'generation.json']) sha256[name] = createHash('sha256').update(await readFile(`${out}/${name}`)).digest('hex');
const catalog = JSON.parse(await readFile(`${out}/catalog.json`));
await writeFile(`${out}/build.json`, JSON.stringify({ renderer: catalog.renderer, dependencies, sha256 }, null, 2) + '\n');
console.log(`Built ${out}`);

import { build } from 'esbuild';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await build({
  absWorkingDir: root,
  entryPoints: [path.join(root, 'web/background-src/index.ts')],
  outfile: 'web/vendor/silk-waves.js',
  bundle: true,
  minify: true,
  format: 'esm',
  target: ['es2020'],
  tsconfig: 'web/background-src/tsconfig.json',
  legalComments: 'eof',
  banner: { js: '/* Silk Waves: Niekes/beautiful-backgrounds (MIT), adapted for Tracewatch. See THIRD_PARTY_NOTICES.md and vendor/licenses/. */' },
});
const licenseDir = path.join(root, 'web/vendor/licenses');
await mkdir(licenseDir, { recursive: true });
for (const pkg of ['lit', 'lit-html', 'lit-element', '@lit/reactive-element']) {
  await copyFile(path.join(root, 'node_modules', pkg, 'LICENSE'), path.join(licenseDir, `${pkg.replaceAll('/', '-')}-BSD.txt`));
}
const packageInfo = JSON.parse(await readFile(path.join(root, 'package-lock.json'), 'utf8'));
await writeFile(path.join(root, 'web/vendor/versions.json'), JSON.stringify({
  upstream: 'https://github.com/Niekes/beautiful-backgrounds',
  commit: 'a44535748fac1be7a29a714f38eca8faea880327',
  component: 'BbSilkWaves',
  lit: packageInfo.packages['node_modules/lit'].version,
  esbuild: packageInfo.packages['node_modules/esbuild'].version,
  modifications: ['12 FPS limit', '1280px canvas-width cap', 'reduced-motion support', 'offscreen pause', 'analysis pause'],
}, null, 2) + '\n');
console.log('Built offline Silk Waves bundle and copied license notices.');

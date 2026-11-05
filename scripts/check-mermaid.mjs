// Validate real Mermaid syntax, rather than only checking Markdown fences.
// Install mermaid@11.4.1 and jsdom@26.1.0 in a temporary prefix, then pass
// that prefix's node_modules directory as the first argument.
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const modules = process.argv[2];
if (!modules) throw new Error('Pass the temporary node_modules directory');
const { JSDOM } = await import(pathToFileURL(path.resolve(modules, 'jsdom/lib/api.js')));
const dom = new JSDOM('');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const { default: mermaid } = await import(
  pathToFileURL(path.resolve(modules, 'mermaid/dist/mermaid.esm.mjs'))
);
mermaid.initialize({ startOnLoad: false });

async function markdownFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await markdownFiles(filename));
    else if (entry.name.endsWith('.md')) files.push(filename);
  }
  return files.sort();
}

let count = 0;
let failures = 0;
for (const filename of ['README.md', ...await markdownFiles('docs')]) {
  const source = await readFile(filename, 'utf8');
  for (const block of source.matchAll(/^```mermaid\s*\n([\s\S]*?)^```/gm)) {
    count++;
    try {
      await mermaid.parse(block[1]);
    } catch (error) {
      failures++;
      const line = source.slice(0, block.index).split('\n').length;
      console.error(`${filename}:${line}: ${error.message}`);
    }
  }
}
dom.window.close();
console.log(`Mermaid ${mermaid.version ?? '11.4.1'}: ${count} diagrams, ${failures} failures`);
if (!count || failures) process.exitCode = 1;

// Bundled for Node: the installed skill needs no node_modules or model client.
import { readFileSync } from 'node:fs';
import { library, validateLang } from './view.jsx';

if (process.argv.includes('--spec')) {
  const rules = [
    'Generate a page for the current question; do not follow a fixed report template.',
    'Children are component expressions/references (including nested Stack, Columns, Section, Disclosure and Tabs), never strings.',
    'Use only literal props and component references. No Query, Mutation, global state or function/action expressions. Local component controls already work.',
    'Use the dataRef values available in this snapshot. Never put invented amounts in text. Text is an agent explanation, not a core metric.',
    'Use at most 256 component nodes, 16 nesting levels and 64 children per container. Tabs has one title per child, at most 12.',
    'Return OpenUI source without Markdown fences. Validation diagnostics can be used for correction.',
  ];
  console.log(JSON.stringify({ version: 2, library: library.toSpec(), prompt: library.prompt({ additionalRules: rules }) }));
} else {
  try {
    const input = JSON.parse(readFileSync(0, 'utf8'));
    const { refs } = validateLang(input.lang, input.kind);
    console.log(JSON.stringify({ valid: true, refs }));
  } catch (error) {
    console.log(JSON.stringify({ valid: false, errors: [{ code: error.code || 'validation', message: error.message, details: error.details || [] }] }));
    process.exitCode = 1;
  }
}

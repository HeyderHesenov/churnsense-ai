import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import jsxA11y from 'eslint-plugin-jsx-a11y-x';
import reactHooks from 'eslint-plugin-react-hooks';
import security from 'eslint-plugin-security';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// Every string a page shows is rendered as text: no way for HTML in.
const HTML = 'Render text, never HTML.';
const htmlSinks = [
  { property: 'innerHTML', message: HTML },
  { property: 'outerHTML', message: HTML },
  { property: 'insertAdjacentHTML', message: HTML },
  { object: 'document', property: 'write', message: HTML },
  { object: 'document', property: 'writeln', message: HTML },
];
// The browser keeps UI preferences only, through lib/preferences.
const STORAGE = 'Use lib/preferences: UI preferences only.';
const storage = [
  { object: 'window', property: 'localStorage', message: STORAGE },
  { object: 'window', property: 'sessionStorage', message: STORAGE },
  { object: 'globalThis', property: 'localStorage', message: STORAGE },
  { object: 'globalThis', property: 'sessionStorage', message: STORAGE },
];

export default defineConfig(
  { ignores: ['dist', 'node_modules', 'playwright-report', 'test-results', 'src/api/schema.d.ts'] },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },
  reactHooks.configs.flat.recommended,
  jsxA11y.configs.strict,
  security.configs.recommended,
  {
    rules: {
      'no-restricted-syntax': [
        'error',
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: HTML },
        // The same prop passed in an object: a spread, or createElement's props.
        { selector: "Property[key.name='dangerouslySetInnerHTML']", message: HTML },
        { selector: "Property[key.value='dangerouslySetInnerHTML']", message: HTML },
      ],
      'no-restricted-properties': ['error', ...htmlSinks, ...storage],
      'no-restricted-globals': [
        'error',
        { name: 'localStorage', message: STORAGE },
        { name: 'sessionStorage', message: STORAGE },
      ],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      // `onClick={() => setOpen(true)}` is how React handlers read; a void
      // value used anywhere else is still an error.
      '@typescript-eslint/no-confusing-void-expression': ['error', { ignoreArrowShorthand: true }],
      // Index access into typed tuples and records is checked by noUncheckedIndexedAccess.
      'security/detect-object-injection': 'off',
    },
  },
  {
    // The one module that may touch storage, and tests, which reset it.
    files: ['src/lib/preferences.ts', 'src/**/*.test.{ts,tsx}', 'src/test/**'],
    rules: { 'no-restricted-properties': ['error', ...htmlSinks] },
  },
  {
    files: ['**/*.config.{js,ts}', 'e2e/**'],
    languageOptions: { globals: { ...globals.node } },
  },
  // Plain JavaScript (this file) is in no TypeScript project: lint it without
  // types. Last, so no rule above switches a typed rule back on for it.
  { files: ['**/*.js'], extends: [tseslint.configs.disableTypeChecked] },
);

import js from '@eslint/js';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import perfectionist from 'eslint-plugin-perfectionist';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import unicorn from 'eslint-plugin-unicorn';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import prettier from 'eslint-config-prettier';

/**
 * The frontend's half of the contract in AGENTS.md. Like ruff on the backend: the
 * broad recommended sets, with individual rules turned off and a note saying why.
 */
export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },

  js.configs.recommended,

  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      unicorn.configs.recommended,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { perfectionist },
    rules: {
      // ---- AGENTS.md rules, enforced ------------------------------------

      // Absolute imports only (`src/api/client`), as on the backend.
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['./*', '../*'],
              message: 'Absolute imports only: use `src/...` (AGENTS.md > Frontend).',
            },
          ],
        },
      ],

      // EventSource cannot set headers, so it cannot carry the auth token.
      'no-restricted-globals': [
        'error',
        {
          message:
            'EventSource cannot set headers or POST. Use fetch + ReadableStream (src/api/events.ts).',
          name: 'EventSource',
        },
      ],

      '@typescript-eslint/no-floating-promises': 'error',

      // Type-only imports never pull a module into the bundle.
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { fixStyle: 'inline-type-imports', prefer: 'type-imports' },
      ],

      // `console.error` stays for the ErrorBoundary.
      'no-console': ['error', { allow: ['error'] }],

      // ---- Ordering ----------------------------------------------------------
      'perfectionist/sort-imports': [
        'error',
        {
          groups: [['builtin', 'external'], 'internal', ['parent', 'sibling', 'index'], 'style'],
          internalPattern: ['^src/.*'],
          newlinesBetween: 'ignore',
          type: 'alphabetical',
        },
      ],
      'perfectionist/sort-jsx-props': ['error', { type: 'alphabetical' }],
      'perfectionist/sort-named-imports': ['error', { type: 'alphabetical' }],
      'perfectionist/sort-objects': ['error', { type: 'alphabetical' }],

      // Not sort-interfaces: the wire types in `src/api/ledger.ts` keep the field
      // order of `backend/app/api/schemas.py`.

      // ---- Accommodations, with reasons ----------------------------------

      // Allows effect cleanups like `() => clearTimeout(t)`.
      '@typescript-eslint/no-confusing-void-expression': ['error', { ignoreArrowShorthand: true }],

      // `?since=${seq}` is a number in a URL, which is the normal case.
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],

      // `while (true)` around `reader.read()` is how you drain a ReadableStream.
      '@typescript-eslint/no-unnecessary-condition': [
        'error',
        { allowConstantLoopConditions: true },
      ],

      // ---- unicorn: off, and why -----------------------------------------

      // `null` is what the backend sends; `undefined` does not survive JSON.
      'unicorn/no-null': 'off',

      // `props` and `ref` are React's vocabulary.
      'unicorn/name-replacements': 'off',

      // `break` in a `switch` inside a loop, as in `keysToInvalidate`, is fine.
      'unicorn/no-break-in-nested-loop': 'off',

      // One-line `/** ... */` field notes are intended.
      'unicorn/single-line-block-comment-style': 'off',

      'unicorn/prefer-ternary': 'off',
      'unicorn/prefer-early-return': 'off',

      // Browser-only.
      'unicorn/prefer-global-this': 'off',

      // `getElementById` is not worse than `querySelector('#id')`.
      'unicorn/prefer-query-selector': 'off',

      // `reduce` is clear for a sum, as in `JournalCard`.
      'unicorn/no-array-reduce': 'off',

      // `[...map.values()]` reads as well as `Iterator#toArray`.
      'unicorn/prefer-iterator-to-array': 'off',

      // The module-level `provider` in `src/api/auth.ts` is the auth seam.
      'unicorn/no-top-level-assignment-in-function': 'off',

      // `response.json().catch(() => null)` is clearer than try/await.
      'unicorn/prefer-await': 'off',

      // Separate guards, each with its own reason, as in `parseMajor`.
      'unicorn/prefer-simple-condition-first': 'off',
      'unicorn/prefer-combined-guards': 'off',

      // ---- unicorn: configured rather than disabled ----------------------

      // `RunList.tsx`, `useLiveUpdates.ts`, `money.ts`.
      'unicorn/filename-case': ['error', { cases: { camelCase: true, pascalCase: true } }],

      // `caught`, so it never shadows a query's `error`.
      'unicorn/catch-error-name': ['error', { name: 'caught' }],
    },
  },

  {
    files: ['**/*.tsx'],
    extends: [jsxA11y.flatConfigs.strict],
    plugins: { react, 'react-refresh': reactRefresh },
    settings: { react: { version: '19.0' } },
    rules: {
      // Names and memos are operator input.
      'react/no-danger': 'error',

      // One component per file. `only-export-components` checks exports;
      // this counts definitions.
      'react/no-multi-comp': ['error', { ignoreStateless: false }],

      // A helper another file needs belongs in a `.ts` module.
      'react-refresh/only-export-components': ['error', { allowConstantExport: false }],
    },
  },

  reactHooks.configs.flat.recommended,

  // Build files run in Node and are not part of the browser program.
  {
    files: ['vite.config.ts', 'eslint.config.js'],
    languageOptions: { globals: globals.node },
    rules: { 'no-restricted-imports': 'off' },
  },
  { files: ['**/*.js'], extends: [tseslint.configs.disableTypeChecked] },

  // Last, so Prettier alone owns formatting.
  prettier,
);

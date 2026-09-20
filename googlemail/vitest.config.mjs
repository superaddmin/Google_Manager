import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    extensions: ['.mjs'],
    globals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      reportsDirectory: 'coverage',
      include: ['src/**/*.mjs'],
      // Prevent coverage regressions; these baselines are not a production acceptance target.
      thresholds: { lines: 26, statements: 25, branches: 31, functions: 18 },
    },
  },
});

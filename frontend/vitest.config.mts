import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/** The `lib/*.test.ts` suite is plain TS with no JSX and runs fine on
 *  Vitest's default (Node, no plugins). Component tests (`*.test.tsx`) need
 *  the React plugin for JSX transform — added here rather than a per-file
 *  workaround since Vitest resolves this config for every test file anyway.
 *  Environment stays the Vitest default (Node) globally; component tests opt
 *  into DOM via a `// @vitest-environment jsdom` pragma at the top of the
 *  file, so the existing `fetch`-stubbing tests in `lib/` are unaffected.
 *
 *  The `@/*` alias mirrors tsconfig.json's `paths` — Vitest doesn't read
 *  tsconfig path mappings on its own, so any test that imports through the
 *  alias (as the component under test here does) needs it resolved here too.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": import.meta.dirname },
  },
});

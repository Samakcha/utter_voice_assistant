import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // React Bits components, copied as published (reactbits.dev): their "latest settings in a
    // ref" pattern trips the strict react-hooks rules, which is fine for these
    "src/components/reactbits/**",
  ]),
]);

export default eslintConfig;

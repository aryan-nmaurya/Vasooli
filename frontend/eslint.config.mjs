import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

/**
 * Next.js recommended rules plus TypeScript.
 *
 * Uses the native flat configs these packages export. The FlatCompat shim is the older
 * route and crashes on ESLint 9 with this version, which is worth knowing before
 * reaching for it again.
 *
 * Deliberately close to the defaults: a bespoke rule set is one more thing to maintain,
 * and the point is catching real mistakes — unused variables, bad hook dependencies,
 * unescaped entities — not enforcing a house style the linter already has a view on.
 */
const config = [
  ...coreWebVitals,
  ...typescript,
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  {
    rules: {
      // Unused arguments are often deliberate — a callback signature you have to
      // match. An underscore prefix is how you say so.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;

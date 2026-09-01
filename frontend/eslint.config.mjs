import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// The plan's standing rule: "the frontend holds no business logic... any
// calculation in web/ is a defect." Every amount the UI shows must already
// be a final number handed back by the API; this rule catches the case
// where someone reaches for +/-/*//,% on something that looks like a money
// field instead of asking the backend for the number. lib/money.ts and
// lib/scale.ts are the two deliberate, reviewed exceptions: one formats a
// final number for display, the other turns one into a bar height. Neither
// derives a financial fact (see their file comments).
const amountFieldPattern =
  "paise|amount|payout|credit|debit|residual|balance|gross|net|fee|tax|recognised|blocked|unrecognised";

const noAmountArithmetic = {
  rules: {
    "no-restricted-syntax": [
      "error",
      {
        selector: `BinaryExpression[operator=/^[+\\-*/%]$/] > Identifier[name=/${amountFieldPattern}/i]`,
        message:
          "No arithmetic over amount-shaped fields in web/ -- ask the API for the computed number instead (see lib/money.ts).",
      },
      {
        selector: `BinaryExpression[operator=/^[+\\-*/%]$/] > MemberExpression[property.name=/${amountFieldPattern}/i]`,
        message:
          "No arithmetic over amount-shaped fields in web/ -- ask the API for the computed number instead (see lib/money.ts).",
      },
      {
        selector: `AssignmentExpression[operator=/^[+\\-*/%]=$/] > Identifier[name=/${amountFieldPattern}/i]`,
        message:
          "No arithmetic over amount-shaped fields in web/ -- ask the API for the computed number instead (see lib/money.ts).",
      },
    ],
  },
};

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["**/*.{ts,tsx}"],
    ignores: ["lib/api/schema.d.ts", "lib/money.ts", "lib/scale.ts"],
    ...noAmountArithmetic,
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;

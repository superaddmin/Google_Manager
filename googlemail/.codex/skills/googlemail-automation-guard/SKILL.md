---
name: googlemail-automation-guard
description: Use when working in F:\Google_Manager\googlemail or with this Node.js ESM + Playwright Google account 2FA/recovery-email automation project, especially changes involving account parsing, TOTP generation, Playwright login/security-setting flows, output logs/results, browser-data, docs, or project .codex agents/rules. Enforces sensitive-data redaction, authorization checks for live account operations, and minimal static-first verification.
---

# Googlemail Automation Guard

Use this skill before editing, testing, reviewing, or documenting this repository.

## Load When Needed

- Read `.codex/context/project-map.md` for architecture, modules, data flow, dependencies, and sensitive paths.
- Read `.codex/workflows/default.md` for verification and command safety tiers.
- Read `docs/README.md`, `docs/usage.md`, and `docs/configuration.md` only when changing behavior, commands, or docs.

## Core Workflow

1. Classify the task.
   - Static/doc/config change: proceed with ordinary repo workflow.
   - TOTP/account parsing: prefer synthetic or redacted data.
   - Playwright/Google page flow: inspect logs/screenshots first.
   - Live login, 2FA change, recovery-email change, or batch run: require explicit user authorization in the current task.
2. Protect sensitive data.
   - Do not echo full emails, passwords, recovery emails, TOTP secrets, current TOTP codes, cookies, browser profile data, result lines, or screenshots.
   - Redact examples as `u***@example.com`, `<PASSWORD>`, `<BASE32_SECRET>`, `<TOTP_CODE>`.
3. Keep changes minimal.
   - Preserve ESM `.mjs` style and existing Chinese log style.
   - Do not rewrite `google-automator.mjs` wholesale.
   - Do not add dependencies unless there is a clear project-local reason.
4. Verify safely.
   - Prefer `node --check src/<file>.mjs`.
   - Treat `node src/verify-2fa.mjs` as sensitive because it reads the real account file; output is always redacted.
   - Treat `npm start`, `npm run test-login`, `node src/main.mjs`, and `node src/test-login.mjs` as live account operations.

## TOTP Guardrails

- Keep TOTP generation on `otplib` v13's default Base32 decoding path; do not convert Base32 secrets to UTF-8 bytes.
- Use `generateSync`/`verifySync` with epoch seconds and `epochTolerance`; decoded secrets must contain at least 16 bytes.
- Do not log current TOTP codes, full old/new secrets, or result lines; use `<TOTP_CODE>` and `<BASE32_SECRET>`.
- If live Google Authenticator verification keeps rejecting codes while offline checks pass, stop before batch execution and classify it as likely account-state/key mismatch until proven otherwise.

## Boundaries

- Do not enhance evasion, stealth, anti-detection, abuse scaling, or unauthorized account takeover capabilities.
- Do not create new exfiltration paths for credentials, screenshots, browser data, or logs.
- Do not save secrets into `.codex`, docs, examples, commits, issue text, or final replies.
- Do not delete `browser-data/` or `output/` without inventory and explicit user confirmation.

## Preferred Outputs

For changes, report:

- 变更点：files and intent.
- 敏感影响：whether credentials/TOTP/browser state are touched.
- 验证：static commands actually run and results.
- 未执行项：any live/sensitive command intentionally not run.

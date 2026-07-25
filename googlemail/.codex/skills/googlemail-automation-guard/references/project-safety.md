# Googlemail Safety Reference

## Sensitive Commands

- `npm start`: batch account automation; may modify real 2FA/recovery email.
- `node src/main.mjs`: same as npm start.
- `npm run test-login`: headed single-account real flow; may modify the account.
- `node src/test-login.mjs`: same as test-login.
- `node src/verify-2fa.mjs`: offline but reads the account file; output is always redacted.

## Sensitive Paths

- `宝贝信息-*.txt`
- `output/result.txt`
- `output/progress.json`
- `output/logs/*.log`
- `output/debug-*.png`
- `browser-data/`
- `.env`

## Redaction Examples

- Email: `u***@gmail.com`
- Recovery email: `r***@example.com`
- Password: `<PASSWORD>`
- Secret: `<BASE32_SECRET>` or first 4 chars only when necessary
- TOTP: `<TOTP_CODE>`

## TOTP Safety Notes

- Use otplib v13's Base32-compatible TOTP generation only.
- Never log generated TOTP codes or full Base32 secrets.
- Live Google rejection after local verification usually indicates account-state/key mismatch; do not continue to batch processing without confirming the target account state.

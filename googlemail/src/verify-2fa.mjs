import { parseAccounts } from './account-parser.mjs';
import { generateTOTP, getBase32DecodedByteLength, verifyTOTP } from './totp.mjs';
import * as config from './config.mjs';

const accounts = parseAccounts(config.ACCOUNTS_FILE);

function maskEmail(email) {
  const [name, domain] = email.split('@');
  if (!domain) return '<EMAIL>';
  const visible = name.slice(0, 2);
  return `${visible}***@${domain}`;
}

function maskSecret() {
  return '<BASE32_SECRET>';
}

function maskTOTP() {
  return '<TOTP_CODE>';
}

console.log('═'.repeat(70));
console.log('  Google 账号 2FA 密钥验证报告');
console.log('═'.repeat(70));
console.log(`  总账号数: ${accounts.length}`);
console.log(`  验证时间: ${new Date().toISOString()}`);
console.log('═'.repeat(70));
console.log();

let passCount = 0;
let failCount = 0;
const issues = [];

for (let i = 0; i < accounts.length; i++) {
  const { email, oldSecret } = accounts[i];
  const idx = String(i + 1).padStart(2, ' ');
  const emailPad = maskEmail(email).padEnd(38);

  const cleanSecret = oldSecret.replace(/\s+/g, '');
  const decodedBytes = getBase32DecodedByteLength(cleanSecret);
  const isBase32 = /^[A-Za-z2-7]+=*$/.test(cleanSecret);
  const isValidLen = decodedBytes >= 16;

  let basicCheck = true;
  const warnings = [];

  if (!isBase32) {
    basicCheck = false;
    warnings.push(`含非Base32字符`);
  }
  if (!isValidLen) {
    basicCheck = false;
    warnings.push(`Base32 解码后仅 ${decodedBytes} 字节`);
  }

  const totp = generateTOTP(cleanSecret);
  const verified = totp ? verifyTOTP(totp, cleanSecret) : false;

  if (basicCheck && totp && verified) {
    console.log(`  [${idx}] ✅ ${emailPad} | TOTP: ${maskTOTP()} | 解码字节: ${decodedBytes}`);
    passCount++;
  } else {
    console.log(`  [${idx}] ❌ ${emailPad} | 密钥: ${maskSecret()} | 解码字节: ${decodedBytes} | ${warnings.join(', ')}`);
    failCount++;
    issues.push({ idx: i + 1, email, warnings: warnings.join(', ') });
  }
}

console.log();
console.log('═'.repeat(70));
console.log(`  结果: ✅ ${passCount} 通过 | ❌ ${failCount} 失败`);
console.log('═'.repeat(70));

if (issues.length > 0) {
  console.log();
  console.log('  问题账号详情:');
  for (const issue of issues) {
    console.log(`    ${issue.idx}. ${maskEmail(issue.email)}`);
    console.log(`       密钥: ${maskSecret()}`);
    console.log(`       问题: ${issue.warnings}`);
  }
}

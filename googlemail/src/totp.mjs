import { generateSync, verifySync } from 'otplib';
import crypto from 'crypto';

const BASE32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const TOTP_PERIOD_SECONDS = 30;
const MIN_SECRET_BYTES = 16;

const TOTP_OPTIONS = {
  period: TOTP_PERIOD_SECONDS,
  digits: 6,
  algorithm: 'sha1',
};

function isValidBase32(secret) {
  return /^[A-Za-z2-7]+=*$/.test(secret);
}

function decodedBase32ByteLength(secret) {
  const unpaddedLength = secret.replace(/=+$/, '').length;
  return Math.floor((unpaddedLength * 5) / 8);
}

export function getBase32DecodedByteLength(secret) {
  if (typeof secret !== 'string') return 0;
  const cleanSecret = secret.trim().replace(/\s+/g, '');
  if (!isValidBase32(cleanSecret)) return 0;
  return decodedBase32ByteLength(cleanSecret);
}

function normalizeSecret(secret) {
  if (typeof secret !== 'string') return null;

  const cleanSecret = secret.trim().replace(/\s+/g, '');
  if (!isValidBase32(cleanSecret)) return null;
  if (getBase32DecodedByteLength(cleanSecret) < MIN_SECRET_BYTES) return null;
  return cleanSecret;
}

export function generateTOTP(secret, epoch) {
  if (typeof secret !== 'string') {
    console.error('  [TOTP] 密钥类型无效: <BASE32_SECRET>');
    return null;
  }

  const cleanSecret = normalizeSecret(secret);
  if (!cleanSecret) {
    console.error('  [TOTP] 密钥不是有效的 Base32，或解码后不足 16 字节: <BASE32_SECRET>');
    return null;
  }

  try {
    return generateSync({
      secret: cleanSecret,
      ...TOTP_OPTIONS,
      ...(Number.isFinite(epoch) ? { epoch } : {}),
    });
  } catch (error) {
    console.error(`  [TOTP] 密钥无效: <BASE32_SECRET> 错误: ${error.message}`);
    return null;
  }
}

export function generateBase32Secret(input = 20) {
  if (Buffer.isBuffer(input)) {
    if (input.length === 0) throw new RangeError('密钥字节不能为空');
  } else if (!Number.isInteger(input) || input <= 0) {
    throw new RangeError('密钥字节数必须是正整数');
  }

  const bytes = Buffer.isBuffer(input) ? input : crypto.randomBytes(input);
  let bits = '';
  let secret = '';

  for (const byte of bytes) {
    bits += byte.toString(2).padStart(8, '0');
  }

  for (let i = 0; i + 5 <= bits.length; i += 5) {
    secret += BASE32_ALPHABET[parseInt(bits.slice(i, i + 5), 2)];
  }

  if (bits.length % 5 !== 0) {
    secret += BASE32_ALPHABET[parseInt(bits.slice(bits.length - (bits.length % 5)).padEnd(5, '0'), 2)];
  }

  return secret;
}

export function getTOTPTimeRemaining() {
  const elapsed = Math.floor(Date.now() / 1000) % TOTP_PERIOD_SECONDS;
  return TOTP_PERIOD_SECONDS - elapsed;
}

export function verifyTOTP(token, secret, epoch) {
  if (typeof token !== 'string' || !/^\d{6}$/.test(token) || typeof secret !== 'string') {
    return false;
  }

  const cleanSecret = normalizeSecret(secret);
  if (!cleanSecret) return false;

  try {
    return verifySync({
      secret: cleanSecret,
      token,
      epochTolerance: 60,
      ...TOTP_OPTIONS,
      ...(Number.isFinite(epoch) ? { epoch } : {}),
    }).valid;
  } catch {
    return false;
  }
}

export function maskEmail(email) {
  if (typeof email !== 'string') return '<EMAIL>';

  const normalized = email.trim();
  const atIndex = normalized.lastIndexOf('@');
  if (atIndex <= 0 || atIndex === normalized.length - 1) return '<EMAIL>';

  const name = normalized.slice(0, atIndex);
  const domain = normalized.slice(atIndex + 1);
  const visible = name.slice(0, Math.min(2, name.length));
  return `${visible}***@${domain}`;
}

export function redactSensitiveText(value, knownValues = []) {
  let text = String(value ?? '');

  const values = [...knownValues]
    .filter(item => typeof item === 'string' && item.length > 0)
    .sort((a, b) => b.length - a.length);

  for (const item of values) {
    text = text.split(item).join('<REDACTED>');
  }

  text = text.replace(
    /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi,
    match => maskEmail(match),
  );
  text = text.replace(/\b[A-Z2-7]{16,}(?:={0,6})?\b/gi, '<BASE32_SECRET>');
  text = text.replace(/\b\d{6}\b/g, '<TOTP_CODE>');

  return text;
}

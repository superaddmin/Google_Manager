import fs from 'fs';
import path from 'path';

export function parseAccounts(filePath) {
  try {
    if (!fs.existsSync(filePath)) {
      console.error(`[错误] 账号文件不存在: ${filePath}`);
      return [];
    }
    const content = fs.readFileSync(filePath, 'utf-8');
    const accounts = [];
    const lines = content.split(/\r?\n/);
    for (const [index, rawLine] of lines.entries()) {
      const line = rawLine.trim();
      if (!line) continue;

      const parts = line.trim().split('----');
      if (parts.length !== 4) {
        console.warn(`[警告] 跳过格式异常的第 ${index + 1} 行（应为 4 个字段）`);
        continue;
      }
      accounts.push({
        email: parts[0].trim(),
        password: parts[1].trim(),
        recoveryEmail: parts[2].trim(),
        oldSecret: parts[3].trim(),
      });
    }

    if (accounts.length === 0) {
      console.warn('[警告] 账号文件为空或所有行格式异常');
    }

    return accounts;
  } catch (err) {
    console.error(`[错误] 读取账号文件失败: ${err.message}`);
    return [];
  }
}

export function loadProgress(filePath) {
  if (!fs.existsSync(filePath)) {
    return { completed: [], failed: [], lastIndex: -1 };
  }

  let progress;
  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    progress = JSON.parse(content);
  } catch (error) {
    throw new Error(`读取进度文件失败: ${error.message}`, { cause: error });
  }

  const isStringArray = value => (
    Array.isArray(value) && value.every(item => typeof item === 'string')
  );
  if (
    !progress
    || !isStringArray(progress.completed)
    || !isStringArray(progress.failed)
    || !Number.isInteger(progress.lastIndex)
    || progress.lastIndex < -1
  ) {
    throw new Error('进度文件结构无效，请保留原文件并人工检查');
  }

  return {
    completed: [...new Set(progress.completed)],
    failed: [...new Set(progress.failed)],
    lastIndex: progress.lastIndex,
  };
}

export async function saveProgress(filePath, progress) {
  const tempFilePath = `${filePath}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
  try {
    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) {
      await fs.promises.mkdir(dir, { recursive: true });
    }
    await fs.promises.writeFile(tempFilePath, JSON.stringify(progress, null, 2), 'utf8');
    await fs.promises.rename(tempFilePath, filePath);
  } catch (err) {
    await fs.promises.unlink(tempFilePath).catch(() => {});
    throw new Error(`保存进度文件失败: ${err.message}`, { cause: err });
  }
}

export async function appendResult(filePath, line) {
  try {
    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) {
      await fs.promises.mkdir(dir, { recursive: true });
    }
    await fs.promises.appendFile(filePath, line + '\n', 'utf8');
  } catch (err) {
    throw new Error(`写入结果文件失败: ${err.message}`, { cause: err });
  }
}

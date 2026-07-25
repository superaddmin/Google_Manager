import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { parseAccounts, loadProgress, saveProgress, appendResult } from '../src/account-parser.mjs';
import fs from 'fs';
import os from 'os';
import path from 'path';

describe('Account Parser Module', () => {
  let tempDir;

  beforeEach(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'googlemail-account-parser-'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (fs.existsSync(tempDir)) {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  describe('parseAccounts', () => {
    it('should parse valid account file', () => {
      const accountsFile = path.join(tempDir, 'accounts.txt');
      fs.writeFileSync(accountsFile, [
        'email1@gmail.com----password1----recovery1@test.com----BASE32SECRET1',
        'email2@gmail.com----password2----recovery2@test.com----BASE32SECRET2',
      ].join('\n'));

      const accounts = parseAccounts(accountsFile);
      expect(accounts).toBeDefined();
      expect(accounts.length).toBe(2);
      expect(accounts[0].email).toBe('email1@gmail.com');
      expect(accounts[0].password).toBe('password1');
      expect(accounts[0].recoveryEmail).toBe('recovery1@test.com');
      expect(accounts[0].oldSecret).toBe('BASE32SECRET1');
    });

    it('should skip lines with invalid format', () => {
      const accountsFile = path.join(tempDir, 'accounts.txt');
      fs.writeFileSync(accountsFile, [
        'email1@gmail.com----password1----recovery1@test.com----BASE32SECRET1',
        'invalid-line-with-only-three-parts----password----recovery',
        'email2@gmail.com----password2----recovery2@test.com----BASE32SECRET2',
      ].join('\n'));

      const accounts = parseAccounts(accountsFile);
      expect(accounts.length).toBe(2);
    });

    it('should not include malformed account content in warnings', () => {
      const accountsFile = path.join(tempDir, 'accounts.txt');
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
      fs.writeFileSync(accountsFile, 'private@example.com----<PASSWORD>----invalid');

      parseAccounts(accountsFile);

      const output = warning.mock.calls.flat().join(' ');
      expect(output).toContain('第 1 行');
      expect(output).not.toContain('private@example.com');
      expect(output).not.toContain('<PASSWORD>');
    });

    it('should skip empty lines', () => {
      const accountsFile = path.join(tempDir, 'accounts.txt');
      fs.writeFileSync(accountsFile, [
        '',
        'email1@gmail.com----password1----recovery1@test.com----BASE32SECRET1',
        '',
        'email2@gmail.com----password2----recovery2@test.com----BASE32SECRET2',
        '',
      ].join('\n'));

      const accounts = parseAccounts(accountsFile);
      expect(accounts.length).toBe(2);
    });

    it('should return empty array for non-existent file', () => {
      const accounts = parseAccounts(path.join(tempDir, 'non-existent.txt'));
      expect(accounts).toEqual([]);
    });

    it('should return empty array for empty file', () => {
      const accountsFile = path.join(tempDir, 'empty.txt');
      fs.writeFileSync(accountsFile, '');

      const accounts = parseAccounts(accountsFile);
      expect(accounts).toEqual([]);
    });

    it('should trim whitespace from fields', () => {
      const accountsFile = path.join(tempDir, 'accounts.txt');
      fs.writeFileSync(accountsFile, '  email@gmail.com  ----  password  ----  recovery@test.com  ----  SECRET  ');

      const accounts = parseAccounts(accountsFile);
      expect(accounts.length).toBe(1);
      expect(accounts[0].email).toBe('email@gmail.com');
      expect(accounts[0].password).toBe('password');
      expect(accounts[0].recoveryEmail).toBe('recovery@test.com');
      expect(accounts[0].oldSecret).toBe('SECRET');
    });
  });

  describe('loadProgress', () => {
    it('should load progress from existing file', () => {
      const progressFile = path.join(tempDir, 'progress.json');
      fs.writeFileSync(progressFile, JSON.stringify({
        completed: ['email1@gmail.com', 'email2@gmail.com'],
        failed: ['email3@gmail.com'],
        lastIndex: 2,
      }));

      const progress = loadProgress(progressFile);
      expect(progress.completed).toEqual(['email1@gmail.com', 'email2@gmail.com']);
      expect(progress.failed).toEqual(['email3@gmail.com']);
      expect(progress.lastIndex).toBe(2);
    });

    it('should return default progress for non-existent file', () => {
      const progress = loadProgress(path.join(tempDir, 'non-existent.json'));
      expect(progress).toEqual({ completed: [], failed: [], lastIndex: -1 });
    });

    it('should reject invalid JSON instead of restarting from empty progress', () => {
      const progressFile = path.join(tempDir, 'invalid.json');
      fs.writeFileSync(progressFile, 'invalid json');

      expect(() => loadProgress(progressFile)).toThrow('读取进度文件失败');
    });

    it('should reject malformed progress data', () => {
      const progressFile = path.join(tempDir, 'malformed.json');
      fs.writeFileSync(progressFile, JSON.stringify({
        completed: 'not-an-array',
        failed: 123,
        lastIndex: 'not-a-number',
      }));

      expect(() => loadProgress(progressFile)).toThrow('进度文件结构无效');
    });
  });

  describe('saveProgress', () => {
    it('should save progress to file', async () => {
      const progressFile = path.join(tempDir, 'progress.json');
      const progress = {
        completed: ['email1@gmail.com'],
        failed: [],
        lastIndex: 0,
      };

      await saveProgress(progressFile, progress);

      expect(fs.existsSync(progressFile)).toBe(true);
      const saved = JSON.parse(fs.readFileSync(progressFile, 'utf-8'));
      expect(saved).toEqual(progress);
    });

    it('should create directory if it does not exist', async () => {
      const progressFile = path.join(tempDir, 'subdir', 'progress.json');
      const progress = { completed: [], failed: [], lastIndex: -1 };

      await saveProgress(progressFile, progress);

      expect(fs.existsSync(progressFile)).toBe(true);
    });

    it('should atomically replace existing progress', async () => {
      const progressFile = path.join(tempDir, 'progress.json');
      await saveProgress(progressFile, { completed: [], failed: [], lastIndex: -1 });
      const updated = { completed: ['email1@gmail.com'], failed: [], lastIndex: 0 };
      const rename = vi.spyOn(fs.promises, 'rename');

      await saveProgress(progressFile, updated);

      expect(JSON.parse(fs.readFileSync(progressFile, 'utf-8'))).toEqual(updated);
      expect(fs.readdirSync(tempDir).filter(name => name.endsWith('.tmp'))).toEqual([]);
      expect(rename).toHaveBeenCalledOnce();
    });

    it('should reject when progress cannot be written', async () => {
      vi.spyOn(fs.promises, 'writeFile').mockRejectedValueOnce(new Error('disk full'));

      await expect(saveProgress(path.join(tempDir, 'progress.json'), {
        completed: [],
        failed: [],
        lastIndex: -1,
      })).rejects.toThrow('保存进度文件失败');
    });
  });

  describe('appendResult', () => {
    it('should append line to result file', async () => {
      const resultFile = path.join(tempDir, 'result.txt');

      await appendResult(resultFile, 'line1');
      await appendResult(resultFile, 'line2');

      const content = fs.readFileSync(resultFile, 'utf-8');
      expect(content).toBe('line1\nline2\n');
    });

    it('should create directory if it does not exist', async () => {
      const resultFile = path.join(tempDir, 'subdir', 'result.txt');

      await appendResult(resultFile, 'line1');

      expect(fs.existsSync(resultFile)).toBe(true);
    });

    it('should reject when result cannot be appended', async () => {
      vi.spyOn(fs.promises, 'appendFile').mockRejectedValueOnce(new Error('disk full'));

      await expect(appendResult(path.join(tempDir, 'result.txt'), 'line1'))
        .rejects.toThrow('写入结果文件失败');
    });
  });
});

const emailPattern = /^[^\s@|,;]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/;

export const parseAccountImport = (text) => {
    const accounts = [];
    const errors = [];

    text.split(/\r\n|\n|\r/).forEach((line, index) => {
        if (!line.trim()) return;

        const delimiterMatch = Array.from(line.matchAll(/\||——|----|--/g))
            .find(match => emailPattern.test(line.slice(0, match.index).trim()));
        if (!delimiterMatch) {
            errors.push({ lineNumber: index + 1, message: '邮箱或分隔符格式无效，请使用 |、——、---- 或 --' });
            return;
        }

        const delimiter = delimiterMatch[0];
        const parts = [
            line.slice(0, delimiterMatch.index),
            ...line.slice(delimiterMatch.index + delimiter.length).split(delimiter),
        ];
        const password = parts[1] || '';
        const recovery = parts[2]?.trim() || '';
        if (!password.trim()) {
            errors.push({ lineNumber: index + 1, message: '密码不能为空' });
            return;
        }
        if (recovery && !emailPattern.test(recovery)) {
            errors.push({ lineNumber: index + 1, message: '恢复邮箱格式无效，请检查字段顺序' });
            return;
        }

        accounts.push({
            email: parts[0].trim(),
            password,
            recovery,
            secret: (parts[3] || '').replace(/\s/g, ''),
            remark: parts.slice(4).join(delimiter).trim(),
        });
    });

    return { accounts, errors };
};

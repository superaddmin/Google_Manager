"""邮箱地址的统一规范化工具。"""


def canonicalize_email(value):
    """去除首尾空白并按大小写不敏感的身份规则规范化邮箱。"""
    if not isinstance(value, str):
        raise ValueError('邮箱必须是字符串')
    return value.strip().casefold()

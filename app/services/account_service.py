"""
账号服务模块
提供账号相关的业务逻辑处理
"""
import re
from datetime import datetime

from app import db
from app.models.account import Account, utc_now
from app.models.account_history import AccountHistory
from app.utils.totp import generate_totp, get_remaining_seconds


EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MAX_BATCH_IMPORT_SIZE = 500


def normalize_account_data(data, require_all=False):
    """验证必填字段并规范化账号数据。"""
    if not isinstance(data, dict):
        raise ValueError('账号数据格式错误')

    normalized = dict(data)
    for field, display_name in (('email', '邮箱'), ('password', '密码')):
        if require_all or field in normalized:
            value = normalized.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{display_name}为必填项')

    if 'email' in normalized:
        normalized['email'] = normalized['email'].strip()
        if not EMAIL_PATTERN.fullmatch(normalized['email']):
            raise ValueError('邮箱格式无效')

    for field, display_name in (('recovery', '恢复邮箱'), ('secret', '2FA 密钥'), ('remark', '备注')):
        if field in normalized:
            value = normalized[field]
            if value is None:
                normalized[field] = ''
            elif not isinstance(value, str):
                raise ValueError(f'{display_name}必须是字符串')
            elif field == 'recovery':
                normalized[field] = value.strip()
                if normalized[field] and not EMAIL_PATTERN.fullmatch(normalized[field]):
                    raise ValueError('恢复邮箱格式无效')

    if 'status' in normalized and normalized['status'] not in ('inactive', 'pro', 'locked'):
        raise ValueError('账号状态必须为 inactive、pro 或 locked')
    return normalized


class AccountService:
    """账号服务类"""
    
    @staticmethod
    def get_all_accounts(search='', sold_status=None):
        """
        获取所有账号（支持搜索与出售状态筛选）

        Args:
            search: 搜索关键词
            sold_status: 出售状态筛选（sold/unsold），None 或其他值表示全部

        Returns:
            账号字典列表
        """
        query = Account.query

        if search:
            search_pattern = f'%{search}%'
            query = query.filter(
                db.or_(
                    Account.email.ilike(search_pattern),
                    Account.remark.ilike(search_pattern)
                )
            )

        if sold_status in ('sold', 'unsold'):
            query = query.filter(Account.sold_status == sold_status)

        accounts = query.order_by(Account.created_at.asc()).all()
        return [acc.to_dict() for acc in accounts]

    @staticmethod
    def get_statistics():
        """
        获取账号资产统计信息

        Returns:
            包含总量、状态分布、2FA/恢复邮箱覆盖率和近期趋势的字典
        """
        from datetime import timedelta
        from sqlalchemy import func

        trend_days = 14
        # created_at 以 UTC 存储，changed_at 以本地时间存储，分别按各自时区生成趋势桶
        import_today = utc_now().date()
        sale_today = datetime.now().date()
        import_since = datetime.combine(import_today - timedelta(days=trend_days - 1), datetime.min.time())
        sale_since = datetime.combine(sale_today - timedelta(days=trend_days - 1), datetime.min.time())

        total = Account.query.count()
        sold = Account.query.filter(Account.sold_status == 'sold').count()
        pro = Account.query.filter(Account.status == 'pro').count()
        with_2fa = Account.query.filter(
            Account.secret.isnot(None), Account.secret != ''
        ).count()
        with_recovery = Account.query.filter(
            Account.recovery.isnot(None), Account.recovery != ''
        ).count()

        import_rows = (
            db.session.query(func.date(Account.created_at), func.count())
            .filter(Account.created_at >= import_since)
            .group_by(func.date(Account.created_at))
            .all()
        )
        import_by_date = {str(row[0]): row[1] for row in import_rows}

        sale_rows = (
            db.session.query(func.date(AccountHistory.changed_at), func.count())
            .filter(
                AccountHistory.field_name == 'sold_status',
                AccountHistory.new_value == 'sold',
                AccountHistory.changed_at >= sale_since,
            )
            .group_by(func.date(AccountHistory.changed_at))
            .all()
        )
        sale_by_date = {str(row[0]): row[1] for row in sale_rows}

        recent_imports = [
            {'date': (import_today - timedelta(days=trend_days - 1 - offset)).isoformat(),
             'count': import_by_date.get(
                 (import_today - timedelta(days=trend_days - 1 - offset)).isoformat(), 0)}
            for offset in range(trend_days)
        ]
        recent_sales = [
            {'date': (sale_today - timedelta(days=trend_days - 1 - offset)).isoformat(),
             'count': sale_by_date.get(
                 (sale_today - timedelta(days=trend_days - 1 - offset)).isoformat(), 0)}
            for offset in range(trend_days)
        ]

        return {
            'total': total,
            'sold': sold,
            'unsold': total - sold,
            'pro': pro,
            'standard': total - pro,
            'with2fa': with_2fa,
            'without2fa': total - with_2fa,
            'withRecovery': with_recovery,
            'withoutRecovery': total - with_recovery,
            'recentImports': recent_imports,
            'recentSales': recent_sales,
        }

    @staticmethod
    def _fetch_accounts_by_ids(account_ids):
        """按 ID 列表获取账号，校验列表格式。"""
        if (
            not isinstance(account_ids, list)
            or not account_ids
            or len(account_ids) > MAX_BATCH_IMPORT_SIZE
            or any(not isinstance(item, int) or isinstance(item, bool) for item in account_ids)
        ):
            raise ValueError('账号 ID 列表格式错误')
        ordered_ids = list(dict.fromkeys(account_ids))
        account_map = {
            account.id: account
            for account in Account.query.filter(Account.id.in_(ordered_ids)).all()
        }
        missing_ids = [account_id for account_id in ordered_ids if account_id not in account_map]
        accounts = [account_map[account_id] for account_id in ordered_ids if account_id in account_map]
        return accounts, missing_ids

    @staticmethod
    def batch_delete(account_ids):
        """
        批量删除账号

        Args:
            account_ids: 账号 ID 列表

        Returns:
            删除结果统计
        """
        accounts, missing_ids = AccountService._fetch_accounts_by_ids(account_ids)
        for account in accounts:
            db.session.delete(account)
        db.session.commit()
        return {
            'deleted_count': len(accounts),
            'missing_ids': missing_ids,
        }

    @staticmethod
    def batch_set_sold_status(account_ids, target_status):
        """
        批量设置出售状态并记录变更历史

        Args:
            account_ids: 账号 ID 列表
            target_status: 目标状态（sold/unsold）

        Returns:
            更新结果统计
        """
        if target_status not in ('sold', 'unsold'):
            raise ValueError('出售状态必须为 sold 或 unsold')

        accounts, missing_ids = AccountService._fetch_accounts_by_ids(account_ids)
        updated_count = 0
        unchanged_count = 0
        for account in accounts:
            if account.sold_status == target_status:
                unchanged_count += 1
                continue
            history = AccountHistory(
                account_id=account.id,
                field_name='sold_status',
                old_value=account.sold_status,
                new_value=target_status
            )
            db.session.add(history)
            account.sold_status = target_status
            updated_count += 1
        db.session.commit()
        return {
            'updated_count': updated_count,
            'unchanged_count': unchanged_count,
            'missing_ids': missing_ids,
        }

    @staticmethod
    def batch_set_remark(account_ids, remark):
        """
        批量设置账号备注

        Args:
            account_ids: 账号 ID 列表
            remark: 备注内容

        Returns:
            更新结果统计
        """
        if remark is None:
            remark = ''
        if not isinstance(remark, str):
            raise ValueError('备注必须是字符串')
        remark = remark.strip()
        if len(remark) > 255:
            raise ValueError('备注长度不能超过 255 个字符')

        accounts, missing_ids = AccountService._fetch_accounts_by_ids(account_ids)
        updated_count = 0
        unchanged_count = 0
        for account in accounts:
            if (account.remark or '') == remark:
                unchanged_count += 1
                continue
            account.remark = remark
            updated_count += 1
        db.session.commit()
        return {
            'updated_count': updated_count,
            'unchanged_count': unchanged_count,
            'missing_ids': missing_ids,
        }
    
    @staticmethod
    def get_account_by_id(account_id):
        """
        根据 ID 获取账号
        
        Args:
            account_id: 账号ID
        
        Returns:
            Account 对象或 None
        """
        return db.session.get(Account, account_id)
    
    @staticmethod
    def create_account(data):
        """
        创建账号
        
        Args:
            data: 账号数据字典
        
        Returns:
            创建的账号字典
        
        Raises:
            ValueError: 邮箱已存在
        """
        data = normalize_account_data(data, require_all=True)

        # 检查邮箱是否已存在
        existing = Account.query.filter_by(email=data['email']).first()
        if existing:
            raise ValueError(f"邮箱 {data['email']} 已存在")
        
        account = Account(
            email=data['email'],
            password=data['password'],
            recovery=data.get('recovery', ''),
            secret=data.get('secret', ''),
            remark=data.get('remark', ''),
            status=data.get('status', 'inactive')
        )
        
        db.session.add(account)
        db.session.commit()
        
        return account.to_dict()
    
    @staticmethod
    def batch_import(accounts):
        """
        批量导入账号
        
        Args:
            accounts: 账号数据列表
        
        Returns:
            导入结果统计
        """
        if len(accounts) > MAX_BATCH_IMPORT_SIZE:
            raise ValueError(f'单次最多导入 {MAX_BATCH_IMPORT_SIZE} 个账号')

        success_count = 0
        failed_count = 0
        failed_emails = []
        imported_accounts = []
        existing_emails = {
            email for (email,) in db.session.query(Account.email).all()
        }
        
        for data in accounts:
            try:
                normalized = normalize_account_data(data, require_all=True)
                email = normalized['email']

                # 跳过数据库中或当前批次内已存在的邮箱
                if email in existing_emails:
                    failed_count += 1
                    failed_emails.append(email)
                    continue
                
                account = Account(
                    email=email,
                    password=normalized['password'],
                    recovery=normalized.get('recovery', ''),
                    secret=normalized.get('secret', ''),
                    remark=normalized.get('remark', ''),
                    status='inactive'  # 导入的账号默认为未开启状态
                )
                
                db.session.add(account)
                existing_emails.add(email)
                success_count += 1
                imported_accounts.append(account)
                
            except ValueError:
                failed_count += 1
                failed_email = data.get('email', '未知') if isinstance(data, dict) else '未知'
                failed_emails.append(failed_email if isinstance(failed_email, str) and failed_email else '未知')
        
        # 提交所有成功的记录
        if success_count > 0:
            db.session.commit()
        
        return {
            'success_count': success_count,
            'failed_count': failed_count,
            'failed_emails': failed_emails,
            'accounts': [acc.to_dict() for acc in imported_accounts]
        }
    
    @staticmethod
    def update_account(account_id, data, commit=True):
        """
        更新账号信息
        
        Args:
            account_id: 账号ID
            data: 更新数据字典
        
        Returns:
            更新后的账号字典或 None
        """
        account = db.session.get(Account, account_id)
        if not account:
            return None

        data = normalize_account_data(data)
        
        # 应急锁定状态保护
        if account.status == 'locked' and ('status' in data or 'password' in data or 'secret' in data):
            raise ValueError('账号处于应急锁定状态，禁止修改状态或敏感凭据')

        # 如果更新邮箱，检查是否与其他账号冲突
        if 'email' in data and data['email'] != account.email:
            existing = Account.query.filter_by(email=data['email']).first()
            if existing:
                raise ValueError(f"邮箱 {data['email']} 已被其他账号使用")
        
        # 更新字段并记录历史
        if 'email' in data:
            account.email = data['email']
        if 'password' in data:
            if data['password'] != account.password:
                # 记录密码修改历史
                history = AccountHistory(
                    account_id=account_id,
                    field_name='password',
                    old_value=account.password,
                    new_value=data['password']
                )
                db.session.add(history)
            account.password = data['password']
        if 'recovery' in data:
            if data['recovery'] != account.recovery:
                # 记录恢复邮箱修改历史
                history = AccountHistory(
                    account_id=account_id,
                    field_name='recovery',
                    old_value=account.recovery,
                    new_value=data['recovery']
                )
                db.session.add(history)
            account.recovery = data['recovery']
        if 'secret' in data:
            if data['secret'] != account.secret:
                # 记录 2FA 密钥修改历史
                history = AccountHistory(
                    account_id=account_id,
                    field_name='secret',
                    old_value=account.secret,
                    new_value=data['secret']
                )
                db.session.add(history)
            account.secret = data['secret']
        if 'remark' in data:
            account.remark = data['remark']
        if 'status' in data:
            account.status = data['status']
        
        if commit:
            db.session.commit()
        return account.to_dict()
    
    @staticmethod
    def delete_account(account_id):
        """
        删除账号
        
        Args:
            account_id: 账号ID
        
        Returns:
            是否删除成功
        """
        account = db.session.get(Account, account_id)
        if not account:
            return False
        
        db.session.delete(account)
        db.session.commit()
        return True
    
    @staticmethod
    def toggle_status(account_id):
        """
        切换账号状态
        
        Args:
            account_id: 账号ID
        
        Returns:
            更新后的账号字典或 None
        """
        account = db.session.get(Account, account_id)
        if not account:
            return None
        
        if account.status == 'locked':
            raise ValueError('账号已处于应急锁定状态，禁止切换状态')

        # 切换状态
        account.status = 'inactive' if account.status == 'pro' else 'pro'
        db.session.commit()
        
        return account.to_dict()
    
    @staticmethod
    def toggle_sold_status(account_id):
        """
        切换账号出售状态
        
        Args:
            account_id: 账号ID
        
        Returns:
            更新后的账号字典或 None
        """
        account = db.session.get(Account, account_id)
        if not account:
            return None
        
        # 记录旧状态
        old_status = account.sold_status
        
        # 切换出售状态
        new_status = 'unsold' if account.sold_status == 'sold' else 'sold'
        account.sold_status = new_status
        
        # 记录售出状态变更历史
        history = AccountHistory(
            account_id=account_id,
            field_name='sold_status',
            old_value=old_status,
            new_value=new_status
        )
        db.session.add(history)
        db.session.commit()
        
        return account.to_dict()
    
    @staticmethod
    def get_2fa_code(account_id):
        """
        获取账号的 2FA 验证码
        
        Args:
            account_id: 账号ID
        
        Returns:
            包含验证码和剩余时间的字典，或 None
        """
        account = db.session.get(Account, account_id)
        if not account or not account.secret:
            return None
        
        if account.status == 'locked':
            raise ValueError('账号已处于应急锁定状态，禁止读取 2FA 验证码')

        try:
            code = generate_totp(account.secret)
            remaining = get_remaining_seconds()
            
            return {
                'code': code,
                'expiry': remaining
            }
        except Exception:
            return None

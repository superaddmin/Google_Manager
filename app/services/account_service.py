"""
账号服务模块
提供账号相关的业务逻辑处理
"""
from app import db
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.utils.totp import generate_totp, get_remaining_seconds


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
    return normalized


class AccountService:
    """账号服务类"""
    
    @staticmethod
    def get_all_accounts(search=''):
        """
        获取所有账号（支持搜索）
        
        Args:
            search: 搜索关键词
        
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
        
        accounts = query.order_by(Account.created_at.asc()).all()
        return [acc.to_dict() for acc in accounts]
    
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
                failed_emails.append(failed_email or '未知')
        
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
    def update_account(account_id, data):
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
        
        try:
            code = generate_totp(account.secret)
            remaining = get_remaining_seconds()
            
            return {
                'code': code,
                'expiry': remaining
            }
        except Exception:
            return None

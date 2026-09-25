import argparse
import ipaddress
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app import create_app, db
from app.services.schema_migration import (
    encrypt_existing_sensitive_data,
    import_all_models,
    initialize_database,
    validate_schema,
    validate_sensitive_data,
)


def validate_database(database_url, encryption_key):
    url = make_url(database_url)
    if url.get_backend_name() == 'sqlite':
        if not url.database or url.database == ':memory:' or url.database.startswith('file:'):
            raise ValueError('数据库校验要求既有 SQLite 文件路径')
        source = Path(url.database).resolve(strict=True)
        if not source.is_file():
            raise ValueError('数据库校验要求普通文件')
        url = url.set(database=source.as_uri(), query={'mode': 'ro', 'uri': 'true'})
    engine = create_engine(url)
    try:
        if engine.dialect.name == 'sqlite':
            with engine.connect() as connection:
                if connection.exec_driver_sql('PRAGMA integrity_check').all() != [('ok',)]:
                    raise RuntimeError('SQLite 完整性校验失败')
        validate_schema(engine)
        validate_sensitive_data(engine, encryption_key)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init-db')
    commands.add_parser('validate-db')
    staff_parser = commands.add_parser('create-cdk-staff')
    staff_parser.add_argument('--username', required=True)
    staff_parser.add_argument('--role', choices=['admin', 'operator', 'reviewer', 'support', 'auditor'], required=True)
    staff_parser.add_argument('--scope', action='append', required=True)
    commands.add_parser('validate-cdk')
    rotation_parser = commands.add_parser('rotate-cdk-keys')
    rotation_parser.add_argument('--apply', action='store_true')
    reset_parser = commands.add_parser('reset-admin-login')
    reset_target = reset_parser.add_mutually_exclusive_group(required=True)
    reset_target.add_argument('--ip', help='仅重置指定 IP 的管理员登录失败记录')
    reset_target.add_argument('--all', action='store_true', help='显式重置全部管理员登录失败记录')
    encryption_parser = commands.add_parser('encrypt-sensitive-data')
    encryption_parser.add_argument(
        '--apply', action='store_true',
        help='实际写入；默认仅盘点待加密值',
    )
    arguments = parser.parse_args()
    if arguments.command == 'reset-admin-login' and arguments.ip is not None:
        try:
            ipaddress.ip_address(arguments.ip)
        except ValueError:
            parser.error('--ip 必须是有效的 IPv4 或 IPv6 地址')
    from dotenv import load_dotenv
    load_dotenv()
    if arguments.command == 'validate-db':
        default_database = Path(__file__).resolve().parents[1] / 'instance' / 'accounts.db'
        try:
            validate_database(
                os.environ.get('DATABASE_URL') or 'sqlite:///' + default_database.as_posix(),
                os.environ.get('GMAIL_TOKEN_ENCRYPTION_KEY'),
            )
        except Exception as error:
            print(f'数据库全量校验失败 ({type(error).__name__})')
            raise SystemExit(1) from None
        print('数据库结构、完整性和全部业务密文校验通过（未迁移或修改数据）')
        return
    application = create_app()
    with application.app_context():
        if arguments.command in {'create-cdk-staff', 'validate-cdk', 'rotate-cdk-keys'}:
            from app.services.cdk_maintenance import rotate_keys, validate_cdk
            from app.services.cdk_service import audit, begin_write
            if arguments.command == 'create-cdk-staff':
                from getpass import getpass
                from app.services.cdk_identity import create_staff
                password = getpass('员工密码（至少 12 字符，不会显示）: ')
                if password != getpass('再次输入员工密码: '):
                    raise SystemExit('两次密码不一致，未创建员工')
                begin_write()
                staff = create_staff(arguments.username, password, arguments.role, arguments.scope)
                audit('host-operator', 'staff.bootstrap', staff.id, role=staff.role, scopes=staff.scopes)
                db.session.commit()
                print('充值员工已创建；请使用 /admin/cdk 登录')
            elif arguments.command == 'validate-cdk':
                print('CDK 数据与密钥校验通过: ' + str(validate_cdk()))
            else:
                print(('已轮换' if arguments.apply else '仅盘点，未写入') + ': ' + str(rotate_keys(arguments.apply)))
            return
        if arguments.command == 'reset-admin-login':
            from app.models.request_limit import LoginAttempt
            attempts = LoginAttempt.query
            if arguments.ip is not None:
                attempts = attempts.filter_by(ip=arguments.ip)
            reset_count = attempts.update(
                {'attempts': 0, 'last_attempt': 0, 'banned_until': 0},
                synchronize_session=False,
            )
            db.session.commit()
            print(f'已重置管理员登录失败记录: {reset_count}')
            return
        import_all_models()
        if arguments.command == 'init-db':
            applied = initialize_database(db.engine)
        else:
            counts = encrypt_existing_sensitive_data(
                db.engine,
                application.config.get('GMAIL_TOKEN_ENCRYPTION_KEY'),
                apply=arguments.apply,
            )
    if arguments.command == 'encrypt-sensitive-data':
        mode = '已加密' if arguments.apply else '待加密'
        print(
            f'{mode}: account_values={counts["account_values"]}, '
            f'history_values={counts["history_values"]}, '
            f'recharge_task_values={counts["recharge_task_values"]}'
        )
    elif applied:
        print('数据库初始化完成，已应用迁移: ' + ', '.join(applied))
    else:
        print('数据库初始化完成，结构已是最新版本')


if __name__ == '__main__':
    main()

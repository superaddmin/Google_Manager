import argparse

from app import create_app, db
from app.services.schema_migration import (
    encrypt_existing_sensitive_data,
    import_all_models,
    initialize_database,
)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init-db')
    encryption_parser = commands.add_parser('encrypt-sensitive-data')
    encryption_parser.add_argument(
        '--apply', action='store_true',
        help='实际写入；默认仅盘点待加密值',
    )
    arguments = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    application = create_app()
    with application.app_context():
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

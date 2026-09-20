"""Gmail API OAuth and inbox operations."""
import base64
import json
import os
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, url_for

from app import db
from app.models.gmail_connection import GmailConnection
from app.models.gmail_watch import GmailWatch
from app.models.one_time_token import OneTimeToken

GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.modify']


class OAuthStateManager:
    """服务端 OAuth 状态注册与生命周期管理，解决无头浏览器/后台任务无 Session 隔离的问题。"""
    TTL_SECONDS = 1800  # 30 分钟

    @classmethod
    def register(cls, state: str, account_id: int | None = None, metadata: dict | None = None) -> str:
        OneTimeToken.register('gmail_oauth', state, {
            'account_id': account_id,
            'metadata': metadata or {},
            'created_at': time.time(),
        }, cls.TTL_SECONDS)
        return state

    @classmethod
    def validate(cls, state: str) -> bool:
        record = OneTimeToken.lookup('gmail_oauth', state)
        return bool(record and record.consumed_at is None and record.expires_at > time.time())

    @classmethod
    def consume(cls, state: str) -> dict | None:
        return OneTimeToken.consume('gmail_oauth', state)

    @classmethod
    def clear(cls):
        OneTimeToken.clear('gmail_oauth')


class GmailServiceError(RuntimeError):
    """Raised when Gmail configuration or an API call fails."""


class GmailService:
    @staticmethod
    def _client_config():
        path = current_app.config.get('GMAIL_CLIENT_SECRET_FILE')
        if not path or not os.path.isfile(path):
            raise GmailServiceError('未配置有效的 GMAIL_CLIENT_SECRET_FILE')
        with open(path, 'r', encoding='utf-8') as file:
            return json.load(file)

    @staticmethod
    def _fernet():
        key = current_app.config.get('GMAIL_TOKEN_ENCRYPTION_KEY')
        if not key:
            raise GmailServiceError('未配置 GMAIL_TOKEN_ENCRYPTION_KEY')
        try:
            return Fernet(key.encode('ascii'))
        except (ValueError, TypeError) as error:
            raise GmailServiceError('GMAIL_TOKEN_ENCRYPTION_KEY 必须是有效的 Fernet 密钥') from error

    @classmethod
    def _encrypt(cls, data):
        return cls._fernet().encrypt(json.dumps(data).encode('utf-8')).decode('ascii')

    @classmethod
    def _decrypt(cls, value):
        try:
            return json.loads(cls._fernet().decrypt(value.encode('ascii')).decode('utf-8'))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as error:
            raise GmailServiceError('Gmail 授权凭据无法解密，请重新授权') from error

    @classmethod
    def _flow(cls, state=None):
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(cls._client_config(), scopes=GMAIL_SCOPES, state=state)
        custom_redirect = current_app.config.get('GMAIL_REDIRECT_URI')
        if custom_redirect:
            flow.redirect_uri = custom_redirect
        else:
            flow.redirect_uri = url_for('api.gmail_oauth_callback', _external=True)
        return flow

    @classmethod
    def authorization_url(cls, account_id=None, metadata=None):
        flow = cls._flow()
        authorization_url, state = flow.authorization_url(
            access_type='offline', include_granted_scopes='true', prompt='consent'
        )
        OAuthStateManager.register(state, account_id=account_id, metadata=metadata)
        return authorization_url, state

    @classmethod
    def complete_authorization(cls, code, state):
        flow = cls._flow(state=state)
        flow.fetch_token(code=code)
        credentials = flow.credentials
        email = cls._service_for_credentials(credentials).users().getProfile(userId='me').execute()['emailAddress']
        connection = GmailConnection.query.filter_by(email=email).first()
        if not connection:
            connection = GmailConnection(email=email, token_data='', scopes=' '.join(GMAIL_SCOPES))
            db.session.add(connection)
        connection.token_data = cls._encrypt(json.loads(credentials.to_json()))
        connection.scopes = ' '.join(credentials.scopes or GMAIL_SCOPES)
        db.session.commit()
        return connection

    @staticmethod
    def _service_for_credentials(credentials):
        from googleapiclient.discovery import build
        return build('gmail', 'v1', credentials=credentials, cache_discovery=False)

    @classmethod
    def _service(cls, connection):
        from google.oauth2.credentials import Credentials
        from app.models.account import Account
        if Account.query.filter_by(email=connection.email, status='locked').first():
            raise GmailServiceError('锁定账号禁止访问 Gmail')
        credentials = Credentials.from_authorized_user_info(cls._decrypt(connection.token_data), GMAIL_SCOPES)
        if credentials.expired and credentials.refresh_token:
            from google.auth.transport.requests import Request
            credentials.refresh(Request())
            connection.token_data = cls._encrypt(json.loads(credentials.to_json()))
            db.session.commit()
        return cls._service_for_credentials(credentials)

    @classmethod
    def list_messages(cls, connection, query='', page_token=None, max_results=20):
        response = cls._service(connection).users().messages().list(
            userId='me', labelIds=['INBOX'], q=query, pageToken=page_token,
            maxResults=max_results
        ).execute()
        messages = [cls.get_message(connection, item['id'], metadata_only=True)
                    for item in response.get('messages', [])]
        return {'messages': messages, 'nextPageToken': response.get('nextPageToken')}

    @classmethod
    def get_message(cls, connection, message_id, metadata_only=False):
        request_args = {'userId': 'me', 'id': message_id, 'format': 'metadata' if metadata_only else 'full'}
        if metadata_only:
            request_args['metadataHeaders'] = ['Subject', 'From', 'To', 'Date']
        response = cls._service(connection).users().messages().get(**request_args).execute()
        return cls._message_dict(response)

    @classmethod
    def modify_message(cls, connection, message_id, add=None, remove=None):
        response = cls._service(connection).users().messages().modify(
            userId='me', id=message_id,
            body={'addLabelIds': add or [], 'removeLabelIds': remove or []}
        ).execute()
        return cls._message_dict(response)

    @classmethod
    def list_labels(cls, connection):
        response = cls._service(connection).users().labels().list(userId='me').execute()
        return response.get('labels', [])

    @classmethod
    def watch(cls, connection):
        topic_name = current_app.config.get('GMAIL_PUBSUB_TOPIC')
        if not topic_name:
            raise GmailServiceError('未配置 GMAIL_PUBSUB_TOPIC')
        response = cls._service(connection).users().watch(
            userId='me',
            body={'labelIds': ['INBOX'], 'topicName': topic_name},
        ).execute()
        expiration = response.get('expiration')
        expiration_at = None
        if expiration:
            expiration_at = datetime.fromtimestamp(
                int(expiration) / 1000,
                tz=timezone.utc,
            ).replace(tzinfo=None)
        watch = GmailWatch.query.filter_by(connection_id=connection.id).first()
        if not watch:
            watch = GmailWatch(connection_id=connection.id, topic_name=topic_name)
            db.session.add(watch)
        watch.topic_name = topic_name
        if not watch.history_id:
            watch.history_id = str(response.get('historyId')) if response.get('historyId') else None
        watch.expiration_at = expiration_at
        watch.active = True
        db.session.commit()
        return watch

    @classmethod
    def process_notification(cls, email, history_id):
        connection = GmailConnection.query.filter_by(email=email).first()
        if not connection:
            return None
        watch = GmailWatch.query.filter_by(connection_id=connection.id, active=True).first()
        if not watch:
            return {'connectionId': connection.id, 'messageIds': [], 'ignored': True}
        try:
            notified = int(history_id)
            previous = int(watch.history_id or 0)
        except (ValueError, TypeError) as error:
            raise GmailServiceError('无效的 Gmail 历史编号') from error
        if notified <= previous:
            return {'connectionId': connection.id, 'messageIds': [], 'ignored': True}
        starting_cursor = watch.history_id
        message_ids = []
        page_token = None
        seen_tokens = set()
        full_sync = not starting_cursor
        final_cursor = str(history_id)
        while True:
            try:
                if full_sync:
                    response = cls._service(connection).users().messages().list(
                        userId='me', labelIds=['INBOX'], maxResults=100, pageToken=page_token,
                    ).execute()
                    message_ids.extend(item['id'] for item in response.get('messages', []))
                else:
                    arguments = {'userId': 'me', 'startHistoryId': starting_cursor,
                                 'historyTypes': ['messageAdded', 'labelAdded', 'labelRemoved']}
                    if page_token:
                        arguments['pageToken'] = page_token
                    response = cls._service(connection).users().history().list(**arguments).execute()
                    for item in response.get('history', []):
                        for kind in ('messagesAdded', 'labelsAdded', 'labelsRemoved'):
                            message_ids.extend(event['message']['id'] for event in item.get(kind, [])
                                               if event.get('message', {}).get('id'))
                    final_cursor = str(max(int(response.get('historyId') or history_id), notified))
            except Exception as error:
                if not full_sync and getattr(getattr(error, 'resp', None), 'status', None) == 404:
                    full_sync = True
                    page_token = None
                    seen_tokens.clear()
                    message_ids.clear()
                    continue
                raise
            page_token = response.get('nextPageToken')
            if not page_token:
                break
            if page_token in seen_tokens:
                raise GmailServiceError('Gmail 分页令牌重复，游标未推进')
            seen_tokens.add(page_token)

        from app.services.gmail_rule_service import GmailRuleService
        unique_ids = list(dict.fromkeys(message_ids))
        summary = {'matched': 0, 'succeeded': 0, 'pendingConfirmation': 0, 'failed': 0}
        for offset in range(0, len(unique_ids), 100):
            result = GmailRuleService.run_rules(connection, message_ids=unique_ids[offset:offset + 100], max_messages=100)
            for field in summary:
                summary[field] += result.get(field, 0)
            if result.get('failed'):
                raise GmailServiceError('邮件规则执行失败，等待重试，游标未推进')
        GmailWatch.query.filter_by(id=watch.id, history_id=starting_cursor).update(
            {'history_id': final_cursor, 'updated_at': datetime.now(timezone.utc).replace(tzinfo=None)},
            synchronize_session=False,
        )
        db.session.commit()
        return {'connectionId': connection.id, 'messageIds': unique_ids,
                'ruleSummary': summary, 'ignored': False}

    @staticmethod
    def _message_dict(message):
        headers = {item['name'].lower(): item['value'] for item in message.get('payload', {}).get('headers', [])}
        body = ''
        payload = message.get('payload', {})
        if payload.get('body', {}).get('data'):
            body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
        else:
            for part in payload.get('parts', []):
                if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                    body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
                    break
        return {
            'id': message.get('id'), 'threadId': message.get('threadId'),
            'labelIds': message.get('labelIds', []), 'snippet': message.get('snippet', ''),
            'subject': headers.get('subject', ''), 'from': headers.get('from', ''),
            'to': headers.get('to', ''), 'date': headers.get('date', ''), 'body': body,
        }

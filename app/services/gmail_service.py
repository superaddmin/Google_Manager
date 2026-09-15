"""Gmail API OAuth and inbox operations."""
import base64
import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, url_for

from app import db
from app.models.gmail_connection import GmailConnection
from app.models.gmail_watch import GmailWatch

GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.modify']


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
        flow.redirect_uri = url_for('api.gmail_oauth_callback', _external=True)
        return flow

    @classmethod
    def authorization_url(cls):
        flow = cls._flow()
        authorization_url, state = flow.authorization_url(
            access_type='offline', include_granted_scopes='true', prompt='consent'
        )
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

        message_ids = []
        if watch.history_id and str(history_id) != watch.history_id:
            response = cls._service(connection).users().history().list(
                userId='me',
                startHistoryId=watch.history_id,
                historyTypes=['messageAdded', 'labelAdded', 'labelRemoved'],
            ).execute()
            for history_item in response.get('history', []):
                for message_added in history_item.get('messagesAdded', []):
                    message = message_added.get('message', {})
                    if message.get('id'):
                        message_ids.append(message['id'])
        watch.history_id = str(history_id)
        watch.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        summary = None
        if message_ids:
            from app.services.gmail_rule_service import GmailRuleService
            summary = GmailRuleService.run_rules(
                connection,
                message_ids=list(dict.fromkeys(message_ids)),
                max_messages=100,
            )
        return {
            'connectionId': connection.id,
            'messageIds': list(dict.fromkeys(message_ids)),
            'ruleSummary': summary,
            'ignored': False,
        }

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

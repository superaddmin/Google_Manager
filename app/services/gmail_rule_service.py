"""Gmail 规则管理、执行和人工确认服务。"""
import json
from datetime import datetime, timezone

from app import db
from app.models.gmail_rule import GmailRule
from app.models.gmail_task_log import GmailActionConfirmation, GmailTaskLog
from app.services.gmail_service import GmailService


class GmailRuleServiceError(RuntimeError):
    """规则输入或执行状态不合法。"""


class GmailRuleService:
    """提供规则 CRUD、同步执行和确认操作。"""

    ACTION_KEYS = {
        'addLabelIds',
        'removeLabelIds',
        'markRead',
        'markUnread',
        'archive',
        'unarchive',
        'trash',
        'untrash',
    }

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _normalize_actions(cls, value):
        if not isinstance(value, dict):
            raise GmailRuleServiceError('actions 必须是对象')
        unknown = set(value) - cls.ACTION_KEYS
        if unknown:
            raise GmailRuleServiceError(f'不支持的动作: {sorted(unknown)[0]}')

        actions = {}
        for key in ('addLabelIds', 'removeLabelIds'):
            labels = value.get(key, [])
            if not isinstance(labels, list) or len(labels) > 50:
                raise GmailRuleServiceError(f'{key} 必须是最多 50 项的数组')
            if any(not isinstance(label, str) or not label.strip() for label in labels):
                raise GmailRuleServiceError(f'{key} 只能包含非空字符串')
            actions[key] = list(dict.fromkeys(label.strip() for label in labels))

        for key in ('markRead', 'markUnread', 'archive', 'unarchive', 'trash', 'untrash'):
            if key in value and not isinstance(value[key], bool):
                raise GmailRuleServiceError(f'{key} 必须是布尔值')
            if value.get(key) is True:
                actions[key] = True

        if actions.get('markRead') and actions.get('markUnread'):
            raise GmailRuleServiceError('markRead 和 markUnread 不能同时启用')
        if actions.get('archive') and actions.get('unarchive'):
            raise GmailRuleServiceError('archive 和 unarchive 不能同时启用')
        if actions.get('trash') and actions.get('untrash'):
            raise GmailRuleServiceError('trash 和 untrash 不能同时启用')
        if not any(actions.values()):
            raise GmailRuleServiceError('至少配置一个 Gmail 动作')
        return actions

    @classmethod
    def _validate_payload(cls, payload, partial=False):
        if not isinstance(payload, dict):
            raise GmailRuleServiceError('规则请求数据格式错误')
        values = {}
        if not partial or 'name' in payload:
            name = payload.get('name')
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
                raise GmailRuleServiceError('规则名称不能为空且最多 100 个字符')
            values['name'] = name.strip()
        if not partial or 'query' in payload:
            query = payload.get('query', '')
            if not isinstance(query, str) or len(query.strip()) > 500:
                raise GmailRuleServiceError('Gmail 查询语句最多 500 个字符')
            values['search_query'] = query.strip()
        if not partial or 'actions' in payload:
            values['actions'] = cls._normalize_actions(payload.get('actions', {}))
        if not partial or 'enabled' in payload:
            enabled = payload.get('enabled', True)
            if not isinstance(enabled, bool):
                raise GmailRuleServiceError('enabled 必须是布尔值')
            values['enabled'] = enabled
        if not partial or 'priority' in payload:
            priority = payload.get('priority', 100)
            if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 10000:
                raise GmailRuleServiceError('priority 必须是 0 到 10000 的整数')
            values['priority'] = priority
        if not partial or 'requiresConfirmation' in payload:
            requires_confirmation = payload.get('requiresConfirmation', False)
            if not isinstance(requires_confirmation, bool):
                raise GmailRuleServiceError('requiresConfirmation 必须是布尔值')
            values['requires_confirmation'] = requires_confirmation
        return values

    @classmethod
    def create_rule(cls, connection, payload):
        values = cls._validate_payload(payload)
        rule = GmailRule(
            connection_id=connection.id,
            name=values['name'],
            search_query=values['search_query'],
            actions=json.dumps(values['actions'], ensure_ascii=False, sort_keys=True),
            enabled=values['enabled'],
            priority=values['priority'],
            requires_confirmation=values['requires_confirmation'],
        )
        db.session.add(rule)
        db.session.commit()
        return rule

    @classmethod
    def update_rule(cls, rule, payload):
        values = cls._validate_payload(payload, partial=True)
        if not values:
            raise GmailRuleServiceError('至少提供一个需要更新的字段')
        for key, value in values.items():
            if key == 'actions':
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            setattr(rule, key, value)
        db.session.commit()
        return rule

    @staticmethod
    def delete_rule(rule):
        db.session.delete(rule)
        db.session.commit()

    @staticmethod
    def _gmail_labels(actions):
        add_labels = list(actions.get('addLabelIds', []))
        remove_labels = list(actions.get('removeLabelIds', []))
        if actions.get('markRead'):
            remove_labels.append('UNREAD')
        if actions.get('markUnread'):
            add_labels.append('UNREAD')
        if actions.get('archive'):
            remove_labels.append('INBOX')
        if actions.get('unarchive'):
            add_labels.append('INBOX')
        if actions.get('trash'):
            add_labels.append('TRASH')
        if actions.get('untrash'):
            remove_labels.append('TRASH')
        return sorted(set(add_labels)), sorted(set(remove_labels))

    @staticmethod
    def _dump(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _message_ids_for_rule(cls, connection, rule, message_ids, max_messages):
        if message_ids is not None:
            candidates = list(dict.fromkeys(message_ids))[:max_messages]
            if rule.search_query:
                matching = GmailService.list_messages(
                    connection,
                    query=rule.search_query,
                    max_results=max_messages,
                )['messages']
                allowed_ids = {message['id'] for message in matching}
                candidates = [message_id for message_id in candidates if message_id in allowed_ids]
            return candidates
        return [
            message['id']
            for message in GmailService.list_messages(
                connection,
                query=rule.search_query,
                max_results=max_messages,
            )['messages']
        ]

    @classmethod
    def _create_log(cls, connection, rule, message_id, actions, status):
        log = GmailTaskLog(
            connection_id=connection.id,
            rule_id=rule.id,
            message_id=message_id,
            action='modify',
            status=status,
            request_data=cls._dump({'addLabelIds': actions[0], 'removeLabelIds': actions[1]}),
        )
        db.session.add(log)
        db.session.flush()
        return log

    @classmethod
    def run_rules(cls, connection, message_ids=None, max_messages=50, dry_run=False):
        if isinstance(max_messages, bool) or not isinstance(max_messages, int):
            raise GmailRuleServiceError('maxMessages 必须是整数')
        max_messages = max(1, min(max_messages, 100))
        rules = GmailRule.query.filter_by(connection_id=connection.id, enabled=True).order_by(
            GmailRule.priority.asc(), GmailRule.id.asc()
        ).all()
        summary = {'matched': 0, 'succeeded': 0, 'pendingConfirmation': 0, 'failed': 0, 'logs': []}

        for rule in rules:
            actions = cls._gmail_labels(rule.action_config())
            try:
                candidate_ids = cls._message_ids_for_rule(connection, rule, message_ids, max_messages)
            except Exception as error:
                log = cls._create_log(connection, rule, '*', actions, 'failed')
                log.error_message = str(error)[:500]
                log.completed_at = cls._utc_now()
                db.session.commit()
                summary['failed'] += 1
                summary['logs'].append(log.to_dict())
                continue

            for message_id in candidate_ids:
                summary['matched'] += 1
                status = 'dry_run' if dry_run else (
                    'pending_confirmation' if rule.requires_confirmation else 'running'
                )
                log = cls._create_log(connection, rule, message_id, actions, status)
                if dry_run:
                    log.result_data = cls._dump({'dryRun': True})
                    log.completed_at = cls._utc_now()
                    db.session.commit()
                    summary['logs'].append(log.to_dict())
                    continue
                if rule.requires_confirmation:
                    db.session.add(GmailActionConfirmation(task_log_id=log.id))
                    db.session.commit()
                    summary['pendingConfirmation'] += 1
                    summary['logs'].append(log.to_dict())
                    continue

                try:
                    result = GmailService.modify_message(
                        connection,
                        message_id,
                        add=actions[0],
                        remove=actions[1],
                    )
                    log.status = 'succeeded'
                    log.result_data = cls._dump(result)
                    summary['succeeded'] += 1
                except Exception as error:
                    log.status = 'failed'
                    log.error_message = str(error)[:500]
                    summary['failed'] += 1
                log.completed_at = cls._utc_now()
                db.session.commit()
                summary['logs'].append(log.to_dict())
        return summary

    @staticmethod
    def list_logs(connection_id=None, status=None, limit=100):
        limit = max(1, min(limit or 100, 500))
        query = GmailTaskLog.query.order_by(GmailTaskLog.created_at.desc())
        if connection_id is not None:
            query = query.filter_by(connection_id=connection_id)
        if status:
            query = query.filter_by(status=status)
        return query.limit(limit).all()

    @staticmethod
    def get_log(log_id):
        return db.session.get(GmailTaskLog, log_id)

    @classmethod
    def confirm_log(cls, log, reviewer, note=None):
        if log.status != 'pending_confirmation' or not log.confirmation:
            raise GmailRuleServiceError('该任务不在待确认状态')
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise GmailRuleServiceError('缺少确认人')
        try:
            actions = json.loads(log.request_data or '{}')
            result = GmailService.modify_message(
                log.connection,
                log.message_id,
                add=actions.get('addLabelIds', []),
                remove=actions.get('removeLabelIds', []),
            )
            log.status = 'succeeded'
            log.result_data = cls._dump(result)
            log.confirmation.status = 'approved'
            log.confirmation.reviewer = reviewer.strip()[:100]
            log.confirmation.note = note.strip()[:500] if isinstance(note, str) and note.strip() else None
            log.confirmation.reviewed_at = cls._utc_now()
            log.completed_at = cls._utc_now()
            db.session.commit()
            return log
        except Exception as error:
            db.session.rollback()
            log = db.session.get(GmailTaskLog, log.id)
            log.status = 'failed'
            log.error_message = str(error)[:500]
            log.confirmation.status = 'approved'
            log.confirmation.reviewer = reviewer.strip()[:100]
            log.confirmation.reviewed_at = cls._utc_now()
            log.completed_at = cls._utc_now()
            db.session.commit()
            raise GmailRuleServiceError('确认后的 Gmail 动作执行失败') from error

    @classmethod
    def reject_log(cls, log, reviewer, note=None):
        if log.status != 'pending_confirmation' or not log.confirmation:
            raise GmailRuleServiceError('该任务不在待确认状态')
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise GmailRuleServiceError('缺少确认人')
        log.status = 'rejected'
        log.confirmation.status = 'rejected'
        log.confirmation.reviewer = reviewer.strip()[:100]
        log.confirmation.note = note.strip()[:500] if isinstance(note, str) and note.strip() else None
        log.confirmation.reviewed_at = cls._utc_now()
        log.completed_at = cls._utc_now()
        db.session.commit()
        return log

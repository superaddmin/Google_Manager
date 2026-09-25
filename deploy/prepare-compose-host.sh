#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    printf '%s\n' '[错误] 请使用 root 用户或 sudo 执行此脚本。' >&2
    exit 1
fi

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
EXPECTED_UID=10001
EXPECTED_GID=10001
CONTAINER_UID=${COMPOSE_CONTAINER_UID:-${EXPECTED_UID}}
CONTAINER_GID=${COMPOSE_CONTAINER_GID:-${EXPECTED_GID}}
ENV_FILE=${PROJECT_DIR}/.env
CREDENTIALS_FILE=${PROJECT_DIR}/credentials.json

for value in "${CONTAINER_UID}" "${CONTAINER_GID}"; do
    case "${value}" in
        ''|*[!0-9]*)
            printf '%s\n' '[错误] COMPOSE_CONTAINER_UID/GID 必须是数字。' >&2
            exit 1
            ;;
    esac
done

if [[ "${CONTAINER_UID}" != "${EXPECTED_UID}" || "${CONTAINER_GID}" != "${EXPECTED_GID}" ]]; then
    printf '%s\n' '[错误] Compose 镜像固定以 UID/GID 10001:10001 运行，不允许覆盖。' >&2
    exit 1
fi

require_single_regular_file() {
    local path=$1
    local label=$2
    local link_count
    if [[ ! -f "${path}" || -L "${path}" ]]; then
        printf '[错误] %s 必须是普通文件且不能是符号链接：%s\n' "${label}" "${path}" >&2
        exit 1
    fi
    link_count=$(stat -c '%h' -- "${path}")
    if [[ "${link_count}" != '1' ]]; then
        printf '[错误] %s 不能是硬链接文件：%s\n' "${label}" "${path}" >&2
        exit 1
    fi
}

validate_runtime_tree() {
    local directory=$1
    local root_device
    local path
    local path_device
    local link_count

    root_device=$(stat -c '%d' -- "${directory}")
    find -P "${directory}" -xdev -print0 | while IFS= read -r -d '' path; do
        path_device=$(stat -c '%d' -- "${path}")
        if [[ "${path_device}" != "${root_device}" ]]; then
            printf '[错误] 运行目录不能跨文件系统挂载：%s\n' "${path}" >&2
            exit 1
        fi
        if [[ -L "${path}" ]]; then
            printf '[错误] 运行目录及其子项不能包含符号链接：%s\n' "${path}" >&2
            exit 1
        elif [[ -f "${path}" ]]; then
            link_count=$(stat -c '%h' -- "${path}")
            if [[ "${link_count}" != '1' ]]; then
                printf '[错误] 运行文件不能是硬链接：%s\n' "${path}" >&2
                exit 1
            fi
        elif [[ ! -d "${path}" ]]; then
            printf '[错误] 运行目录及其子项只能包含普通目录或普通文件：%s\n' "${path}" >&2
            exit 1
        fi
    done
}

require_single_regular_file "${ENV_FILE}" '.env'
require_single_regular_file "${CREDENTIALS_FILE}" 'credentials.json'

if [[ -L "${PROJECT_DIR}/googlemail" || ( -e "${PROJECT_DIR}/googlemail" && ! -d "${PROJECT_DIR}/googlemail" ) ]]; then
    printf '%s\n' '[错误] googlemail 必须是普通目录。' >&2
    exit 1
fi

for directory in \
    "${PROJECT_DIR}/instance" \
    "${PROJECT_DIR}/googlemail/runtime" \
    "${PROJECT_DIR}/googlemail/output"; do
    if [[ -L "${directory}" || ( -e "${directory}" && ! -d "${directory}" ) ]]; then
        printf '[错误] Compose 运行目录必须是普通目录：%s\n' "${directory}" >&2
        exit 1
    fi
    if [[ -e "${directory}" ]]; then
        validate_runtime_tree "${directory}"
    fi
done

trap 'printf "%s\n" "[错误] 权限准备失败，已停止；可能已有部分权限变更，请排除错误后重试。" >&2' ERR

for directory in \
    "${PROJECT_DIR}/instance" \
    "${PROJECT_DIR}/googlemail/runtime" \
    "${PROJECT_DIR}/googlemail/output"; do
    if [[ ! -e "${directory}" ]]; then
        install -d -m 0700 -o "${CONTAINER_UID}" -g "${CONTAINER_GID}" "${directory}"
    fi
done

chown --no-dereference root:root -- "${ENV_FILE}"
chmod 0600 -- "${ENV_FILE}"
chown --no-dereference "${CONTAINER_UID}:${CONTAINER_GID}" -- "${CREDENTIALS_FILE}"
chmod 0600 -- "${CREDENTIALS_FILE}"

for directory in \
    "${PROJECT_DIR}/instance" \
    "${PROJECT_DIR}/googlemail/runtime" \
    "${PROJECT_DIR}/googlemail/output"; do
    find -P "${directory}" -xdev -type d -exec chown --no-dereference "${CONTAINER_UID}:${CONTAINER_GID}" -- {} +
    find -P "${directory}" -xdev -type f -exec chown --no-dereference "${CONTAINER_UID}:${CONTAINER_GID}" -- {} +
    find -P "${directory}" -xdev -type d -exec chmod 700 -- {} +
    find -P "${directory}" -xdev -type f -exec chmod 600 -- {} +
done

printf 'Compose 宿主目录已准备：UID/GID=%s:%s\n' "${CONTAINER_UID}" "${CONTAINER_GID}"
stat -c '%n mode=%a owner=%u:%g' \
    "${ENV_FILE}" \
    "${CREDENTIALS_FILE}" \
    "${PROJECT_DIR}/instance" \
    "${PROJECT_DIR}/googlemail/runtime" \
    "${PROJECT_DIR}/googlemail/output"

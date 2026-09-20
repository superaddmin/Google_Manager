#!/bin/sh
set -eu

# Keep database, lock, token, and runtime artifacts private to UID 10001.
umask 077
exec "$@"

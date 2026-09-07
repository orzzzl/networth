#!/usr/bin/env bash
# One re-runnable command: writes, reloads, and kicks the KeepAlive LaunchAgent.
set -euo pipefail
exec networth backup install-puller "$@"

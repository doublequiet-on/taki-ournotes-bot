#!/usr/bin/env bash
# CI runner only: retain required CJK fonts without an unconditional mirror refresh.
set -euo pipefail
options=(-o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)
if timeout --kill-after=10s 120s sudo apt-get "${options[@]}" install -y fonts-noto-cjk; then
  exit 0
fi
timeout --kill-after=10s 180s sudo apt-get "${options[@]}" update
timeout --kill-after=10s 120s sudo apt-get "${options[@]}" install -y fonts-noto-cjk

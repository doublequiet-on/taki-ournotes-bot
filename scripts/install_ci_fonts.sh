#!/usr/bin/env bash
# CI runner only: retain required CJK fonts without an unconditional mirror refresh.
set -euo pipefail
options=(-o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)
if timeout --kill-after=10s 120s sudo apt-get "${options[@]}" install -y fonts-noto-cjk; then
  exit 0
fi
# Hosted Ubuntu runners select Azure HTTP first; a stalled payload needs another
# official route, not another attempt against the same mirror. Apt still verifies
# Ubuntu's signed indexes and package hashes.
if [[ -f /etc/apt/apt-mirrors.txt ]]; then
  printf '%s\n' 'https://archive.ubuntu.com/ubuntu/' | sudo tee /etc/apt/apt-mirrors.txt >/dev/null
fi
timeout --kill-after=10s 180s sudo apt-get "${options[@]}" update
timeout --kill-after=10s 120s sudo apt-get "${options[@]}" install -y fonts-noto-cjk

#!/usr/bin/env bash
# Скачивание исходных открытых датасетов в папку sources/ (≈130 МБ)
set -euo pipefail
mkdir -p sources
cd sources
[ -d gitbugs ] || git clone --depth 1 https://github.com/av9ash/gitbugs.git   # баг-репорты, CC BY 4.0
[ -d loghub ]  || git clone --depth 1 https://github.com/logpai/loghub.git    # системные логи
echo "Готово: $(pwd)"

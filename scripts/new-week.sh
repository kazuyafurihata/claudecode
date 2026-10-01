#!/usr/bin/env bash
# 今週（月曜始まり）の週次ログを weekly/YYYY-MM-DD.md として作成する。
# 先週の「来週に延ばしたこと」があれば「今週やること」に引き継ぐ。
set -euo pipefail

cd "$(dirname "$0")/.."

today="${1:-$(date +%F)}"
dow=$(date -d "$today" +%u)                       # 1=月 ... 7=日
monday=$(date -d "$today -$((dow - 1)) days" +%F)
week=$(date -d "$monday" +%G-W%V)
file="weekly/${monday}.md"

if [[ -e "$file" ]]; then
  echo "既にあります: $file"
  exit 0
fi

sed -e "s/{{WEEK}}/${week}/" -e "s/{{MONDAY}}/${monday}/" weekly/TEMPLATE.md > "$file"

# 直前の週次ログから「来週に延ばしたこと」を抜き出して引き継ぐ
prev=$(ls weekly/[0-9]*.md 2>/dev/null | grep -v "$file" | sort | awk -v m="$file" '$0 < m' | tail -n 1 || true)
if [[ -n "$prev" ]]; then
  carried=$(awk '/^## 来週に延ばしたこと/{f=1; next} /^## /{f=0} f && /^- / && !/（担当）やること/' "$prev" \
    | sed -E 's/^- (\[.\] )?/- [ ] /; s/ — .*$//; s/$/（先週から持ち越し）/')
  if [[ -n "$carried" ]]; then
    tmp=$(mktemp)
    awk -v c="$carried" '{print} /^## 今週やること/{getline; print; print c}' "$file" > "$tmp"
    mv "$tmp" "$file"
  fi
fi

echo "作成しました: $file"

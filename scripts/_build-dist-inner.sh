#!/bin/sh
# 内部脚本：由 build-dist.sh 在 nix-shell 环境中调用，不要在 shell 直接运行。
set -e

STAGING="$PWD/build/nix-libs"
rm -rf "$STAGING" dist build/ankilinkers
mkdir -p "$STAGING"

DIRS=$(echo "$NIX_LDFLAGS" | tr ' ' '\n' | grep '^-L' | sed 's/^-L//' | sort -u)
for d in $DIRS; do
  for f in "$d"/*.so.*; do
    [ -e "$f" ] && cp -L "$f" "$STAGING/$(basename "$f")" 2>/dev/null || true
  done
done

# 传递闭包兜底：反复用 ldd 找 not found，从同一组目录补入
round=0
while [ "$round" -lt 5 ]; do
  missing=$(LD_LIBRARY_PATH="$STAGING" ldd "$STAGING"/*.so.* 2>/dev/null \
    | grep "not found" | awk '{print $1}' | sort -u)
  [ -z "$missing" ] && break
  for lib in $missing; do
    found=0
    for d in $DIRS; do
      if [ -e "$d/$lib" ]; then
        cp -L "$d/$lib" "$STAGING/$lib"
        found=1
        break
      fi
    done
    [ "$found" -eq 0 ] && echo "警告：未找到 $lib（如运行正常可忽略）"
  done
  round=$((round + 1))
done
echo ">> 打入系统库 $(ls "$STAGING" | wc -l) 个"

ADDS=""
for f in "$STAGING"/*.so.*; do
  ADDS="$ADDS --add-binary $f:."
done

# 让 PyInstaller 分析 Qt 插件依赖时也能从这些库解析
export LD_LIBRARY_PATH="$STAGING"
# shellcheck disable=SC2086
uv run pyinstaller --clean --noconfirm --onefile --windowed \
  --name ankilinkers --paths src $ADDS launcher.py
echo ">> 产物：dist/ankilinkers"

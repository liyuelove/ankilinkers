#!/bin/sh
# NixOS 单文件打包：把 Qt 运行所需的 /nix/store 系统库一起打进 PyInstaller onefile。
# 用法：sh scripts/build-dist.sh；产物：dist/ankilinkers
set -e
cd "$(dirname "$0")/.."
exec nix-shell -p \
  binutils glibc libglvnd libxkbcommon fontconfig freetype glib dbus zlib \
  xorg.libX11 xorg.libxcb xorg.xcbutilkeysyms xorg.xcbutilrenderutil \
  xorg.xcbutilwm xorg.xcbutilimage \
  wayland expat libpng bzip2 brotli krb5 libidn2 libpsl nghttp2 openssl zstd \
  --run "sh scripts/_build-dist-inner.sh"

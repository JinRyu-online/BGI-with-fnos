#!/usr/bin/env bash
# 打包 nas-app 为 .fpk。
# 用法：
#   ./build.sh                    # 使用 PATH 中的 fnpack
#   FNPACK=/path/to/fnpack ./build.sh   # 指定 fnpack 路径
# 下载 fnpack：https://developer.fnnas.com/docs/cli/fnpack/  （选 linux-amd64 或对应架构）
set -eu

cd "$(dirname "$0")"
FNPACK="${FNPACK:-fnpack}"

if ! command -v "$FNPACK" >/dev/null 2>&1; then
  echo "错误：未找到 fnpack。请下载后放入 PATH，或用 FNPACK=/path/to/fnpack ./build.sh" >&2
  exit 1
fi

echo "==> 在 $(pwd) 执行 fnpack build ..."
"$FNPACK" build

echo ""
echo "==> 完成。生成的 .fpk 位于当前目录："
ls -1 bgi-trigger*.fpk 2>/dev/null || ls -1 *.fpk
echo ""
echo "安装到飞牛 NAS："
echo "  appcenter-cli install-fpk <上述 .fpk 文件>"
echo "  或在飞牛应用中心后台上传。"

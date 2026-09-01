#!/bin/bash
# 构建: 将 web/ 复制为 dist/（纯静态，无打包步骤）
set -e
cd "$(dirname "$0")"
rm -rf dist
mkdir -p dist
cp index.html style.css app.js ai.js ai.css echarts.min.js dist/
echo "dist 已生成: $(ls dist/)"

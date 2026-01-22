#!/bin/bash
# MediaCrawler 服务器启动脚本

cd "$(dirname "$0")"

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先创建虚拟环境"
    exit 1
fi

# 激活虚拟环境
source venv/bin/activate

# 检查端口是否被占用
if lsof -ti:8080 > /dev/null 2>&1; then
    echo "⚠️  端口 8080 已被占用"
    read -p "是否停止并重启? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        lsof -ti:8080 | xargs kill -9 2>/dev/null
        sleep 2
    else
        exit 1
    fi
fi

# 启动服务器
echo "🚀 启动 MediaCrawler 服务器..."
echo "访问地址: http://localhost:8080/dashboard.html"
echo "按 Ctrl+C 停止服务器"
echo ""

uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload

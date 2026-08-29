#!/bin/bash
# stock-alert 盘中监控进程控制脚本

PROJ_DIR="/home/admin/stock-alert"
PID_FILE="$PROJ_DIR/data/monitor.pid"
LOG_FILE="$PROJ_DIR/logs/monitor.out"

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
    if is_running; then
        echo "已在运行 (PID: $(cat "$PID_FILE"))，无需重复启动"
        exit 0
    fi
    cd "$PROJ_DIR" || exit 1
    nohup /usr/bin/python3 run_monitor.py >> logs/monitor.out 2>&1 &
    echo $! > "$PID_FILE"
    echo "启动成功 (PID: $(cat "$PID_FILE"))"
    echo "查看日志: tail -f $LOG_FILE"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
            echo "已停止 (PID: $pid)"
        else
            echo "pid 文件存在但进程已退出，清理中..."
        fi
        rm -f "$PID_FILE"
    else
        # 兜底：pid 文件失效时按进程名清理
        if pgrep -f run_monitor.py > /dev/null 2>&1; then
            pkill -f run_monitor.py
            echo "pid 文件缺失，已通过 pkill 清理残留进程"
        else
            echo "未在运行"
        fi
    fi
}

status() {
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "运行中 (PID: $pid, 启动时长: $(ps -o etime= -p "$pid" | tr -d ' '))"
    else
        echo "未在运行"
    fi
    echo "--- $LOG_FILE 最后 5 行 ---"
    if [ -f "$LOG_FILE" ]; then
        tail -n 5 "$LOG_FILE"
    else
        echo "(日志文件不存在)"
    fi
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac

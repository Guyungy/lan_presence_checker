import time, threading, json, os
from ping3 import ping

# 文件路径
DEVICES_FILE  = "devices.json"
SESSION_FILE  = "session.json"
STATUS_FILE   = "status.json"
HISTORY_FILE  = "history.json"

def load_json(path):
    if not os.path.exists(path):
        return {} if not path.endswith("history.json") else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_devices():
    return load_json(DEVICES_FILE)

def check_online_devices():
    now     = time.strftime("%Y-%m-%d %H:%M:%S")
    devices = load_devices()
    session = load_json(SESSION_FILE)

    # 1️⃣ 扫描在线设备
    status = {}
    for ip in devices:
        if ping(ip, timeout=1):
            status[ip] = {"last_seen": now}
            # 新上线则记录起始时间
            if ip not in session:
                session[ip] = now

    # 2️⃣ 清理下线的 session
    for ip in list(session):
        if ip not in status:
            session.pop(ip)

    save_json(SESSION_FILE, session)

    # 3️⃣ 把起始时间塞进 status
    for ip in status:
        status[ip]["start_time"] = session.get(ip, now)

    save_json(STATUS_FILE, status)

    # 4️⃣ 追加到 history.json
    history = load_json(HISTORY_FILE)
    # 确保是列表
    if not isinstance(history, list):
        history = []
    history.append({"timestamp": now, "online": list(status.keys())})
    # （可选）这里可以裁剪历史，保留最近 N 条
    save_json(HISTORY_FILE, history)

def start_loop(interval=30):
    def loop():
        while True:
            check_online_devices()
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True).start()

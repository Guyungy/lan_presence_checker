import time, threading, json, os, subprocess
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

# 尝试通过 ping3 或系统 "ping" 命令判断 IP 是否在线
def is_online(ip, timeout=1, retries=2):
    # 尝试使用ping3库
    for _ in range(retries):
        try:
            result = ping(ip, timeout=timeout)
            if result is not None and result is not False:
                return True
        except Exception:
            pass
    
    # 如果ping3失败，尝试使用系统ping命令
    ping_params = []
    if os.name == 'nt':  # Windows
        ping_params = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:  # Linux/Mac
        ping_params = ["ping", "-c", "1", "-W", str(timeout), ip]
    
    for _ in range(retries):
        try:
            res = subprocess.run(
                ping_params,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
    
    return False

def check_online_devices():
    now     = time.strftime("%Y-%m-%d %H:%M:%S")
    devices = load_devices()
    session = load_json(SESSION_FILE)
    old_status = load_json(STATUS_FILE)

    # 1️⃣ 扫描在线设备
    status = {}
    scan_errors = []
    
    for ip in devices:
        try:
            # 使用更可靠的检测方法，增加超时和重试
            if is_online(ip, timeout=1.5, retries=2):
                status[ip] = {"last_seen": now}
                # 新上线则记录起始时间
                if ip not in session:
                    session[ip] = now
                    print(f"[INFO] 设备上线: {ip} ({devices.get(ip, {}).get('name', '未知设备')})")
        except Exception as e:
            scan_errors.append(f"{ip}: {str(e)}")
            # 如果检测出错但设备之前是在线的，保持其在线状态
            if ip in old_status and (now_ts := time.time()) - time.mktime(time.strptime(old_status[ip]["last_seen"], "%Y-%m-%d %H:%M:%S")) < 120:
                status[ip] = old_status[ip]
                print(f"[WARN] 设备检测失败但保持在线状态: {ip}")
            continue

    # 记录下线设备
    for ip in old_status:
        if ip not in status and ip in devices:
            print(f"[INFO] 设备下线: {ip} ({devices.get(ip, {}).get('name', '未知设备')})")

    # 2️⃣ 清理下线的 session
    for ip in list(session):
        if ip not in status:
            session.pop(ip)

    try:
        save_json(SESSION_FILE, session)
    except Exception as e:
        print(f"[ERROR] 保存session文件失败: {e}")

    # 3️⃣ 把起始时间塞进 status
    for ip in status:
        status[ip]["start_time"] = session.get(ip, now)

    try:
        save_json(STATUS_FILE, status)
    except Exception as e:
        print(f"[ERROR] 保存status文件失败: {e}")

    # 4️⃣ 追加到 history.json
    try:
        history = load_json(HISTORY_FILE)
        # 确保是列表
        if not isinstance(history, list):
            history = []
            
        # 添加新记录
        history.append({"timestamp": now, "online": list(status.keys())})
        
        # 裁剪历史，保留最近7天的记录
        now_ts = time.time()
        cutoff = now_ts - (7 * 24 * 60 * 60)  # 7天前的时间戳
        
        history = [entry for entry in history 
                  if time.mktime(time.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")) >= cutoff]
        
        save_json(HISTORY_FILE, history)
    except Exception as e:
        print(f"[ERROR] 处理历史记录失败: {e}")
        
    if scan_errors:
        print(f"[WARN] 扫描过程中有{len(scan_errors)}个错误: {', '.join(scan_errors[:3])}{'...' if len(scan_errors) > 3 else ''}")
        
    return status

def start_loop(interval=30):
    def loop():
        consecutive_errors = 0
        last_success = time.time()
        
        while True:
            try:
                start_time = time.time()
                status = check_online_devices()
                end_time = time.time()
                
                # 记录扫描性能
                scan_duration = end_time - start_time
                device_count = len(load_devices())
                online_count = len(status)
                
                print(f"[INFO] 扫描完成: 总设备 {device_count}, 在线 {online_count}, 用时 {scan_duration:.2f}秒")
                
                # 重置错误计数
                consecutive_errors = 0
                last_success = time.time()
                
            except Exception as e:
                consecutive_errors += 1
                print(f"[ERROR] 扫描错误 ({consecutive_errors}连续): {str(e)}")
                
                # 如果连续错误过多且间隔时间较长，尝试恢复
                if consecutive_errors >= 5 and time.time() - last_success > 300:  # 5分钟
                    print(f"[WARN] 检测到连续{consecutive_errors}次错误，尝试恢复...")
                    try:
                        # 尝试加载和保存状态文件，检查文件系统
                        status = load_json(STATUS_FILE)
                        save_json(STATUS_FILE, status)
                        print("[INFO] 文件系统检查正常")
                    except Exception as recovery_error:
                        print(f"[ERROR] 恢复尝试失败: {str(recovery_error)}")
            
            # 动态调整扫描间隔
            actual_interval = interval
            if consecutive_errors > 0:
                # 出错时略微增加间隔
                actual_interval = min(interval * 1.5, 120)  # 最多2分钟
            
            time.sleep(actual_interval)
    
    print(f"[INFO] 启动设备扫描线程，间隔 {interval} 秒")
    threading.Thread(target=loop, daemon=True, name="DeviceScanner").start()

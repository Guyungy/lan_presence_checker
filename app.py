from flask import Flask, jsonify, render_template, request
import json, time, os
import scanner
from datetime import datetime, timedelta

app = Flask(__name__)
scanner.start_loop(interval=30)

# 文件路径常量
DEVICES_FILE = "devices.json"
STATUS_FILE  = scanner.STATUS_FILE
HISTORY_FILE = scanner.HISTORY_FILE

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/device/<ip>")
def device_detail(ip):
    return render_template("detail.html", ip=ip)

@app.route("/api/status")
def api_status():
    devices = load_json(DEVICES_FILE, {})
    status  = load_json(STATUS_FILE, {})
    now_ts  = time.time()

    online  = {}
    offline = {}
    for ip, dev in devices.items():
        name, remark, dtype = dev.get("name",""), dev.get("remark",""), dev.get("type","")
        info = status.get(ip)
        if info:
            # 计算在线时长
            last_seen = info["last_seen"]
            start_time= info.get("start_time", last_seen)
            start_ts  = time.mktime(time.strptime(start_time, "%Y-%m-%d %H:%M:%S"))
            delta     = int(now_ts - start_ts)
            h, rem    = divmod(delta, 3600)
            m, s      = divmod(rem, 60)
            duration  = f"{h:02d}:{m:02d}:{s:02d}"
            online[ip] = {
                "name": name, "remark": remark, "type": dtype,
                "last_seen": last_seen, "duration": duration
            }
        else:
            offline[ip] = {"name": name, "remark": remark, "type": dtype}

    return jsonify({
        "online": online,
        "offline": offline,
        "now": time.strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/api/scan", methods=["POST"])
def api_scan():
    scanner.check_online_devices()
    return api_status()

@app.route("/api/device", methods=["POST"])
def api_device():
    data    = request.get_json() or {}
    ip      = data.get("ip")
    devices = load_json(DEVICES_FILE, {})
    if ip not in devices:
        return jsonify({"success": False, "error": "IP 不存在"}), 400

    # 动态修改
    for field in ("name", "remark", "type"):
        if field in data:
            devices[ip][field] = data[field]
    save_json(DEVICES_FILE, devices)
    return jsonify({"success": True})

@app.route("/api/history/<ip>")
def api_history(ip):
    period  = request.args.get("period", "daily")
    history = load_json(HISTORY_FILE, [])
    now = datetime.now()
    if period == "daily":
        cutoff = now - timedelta(days=1)
    elif period == "weekly":
        cutoff = now - timedelta(weeks=1)
    elif period == "monthly":
        cutoff = now - timedelta(days=30)
    else:
        cutoff = now - timedelta(days=1)

    result = []
    for entry in history:
        ts = datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")
        if ts >= cutoff:
            result.append({
                "timestamp": entry["timestamp"],
                "online": ip in entry["online"]
            })
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)

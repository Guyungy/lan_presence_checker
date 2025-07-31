from flask import Flask, jsonify, render_template, request
import json, time, os
import scanner
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
from sqlalchemy import func, desc
from models import Device, DeviceStatus, DeviceHistory, Network, ScanLog, get_db_session, init_db
import network_scanner

# 加载环境变量
load_dotenv()

# 配置日志
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('app')

# 初始化数据库
init_db()

# 初始化Flask应用
app = Flask(__name__)

# 初始化网络配置并启动扫描
network_scanner.init_networks()
network_scanner.start_scan_loop(interval=int(os.environ.get('SCAN_INTERVAL', 30)))

# 兼容旧版本，保留旧的扫描器
scanner.start_loop(interval=60)  # 降低旧扫描器频率

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
    session = get_db_session()
    now_ts = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # 获取所有设备
        devices = session.query(Device).all()
        
        # 获取在线设备
        online_devices = session.query(Device).join(DeviceStatus).filter(DeviceStatus.is_online == True).all()
        
        # 获取最后一次扫描时间
        last_scan = session.query(func.max(ScanLog.timestamp)).scalar()
        if last_scan:
            last_update = last_scan.strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_update = now_str
        
        # 准备返回数据
        online = {}
        offline = {}
        
        # 统计信息
        stats = {
            "total": len(devices),
            "online": len(online_devices),
            "offline": len(devices) - len(online_devices),
            "types": {}
        }
        
        # 按类型统计
        type_counts = {}
        for device in devices:
            dtype = device.type or "未分类"
            if dtype not in type_counts:
                type_counts[dtype] = {"total": 0, "online": 0}
            type_counts[dtype]["total"] += 1
        
        # 按类型统计在线设备
        for device in online_devices:
            dtype = device.type or "未分类"
            if dtype in type_counts:
                type_counts[dtype]["online"] += 1
        
        # 计算每种类型的在线率
        for dtype, counts in type_counts.items():
            if counts["total"] > 0:
                counts["online_rate"] = round((counts["online"] / counts["total"]) * 100, 1)
            else:
                counts["online_rate"] = 0
        
        stats["types"] = type_counts
        
        # 计算总在线率
        if stats["total"] > 0:
            stats["online_rate"] = round((stats["online"] / stats["total"]) * 100, 1)
        else:
            stats["online_rate"] = 0
        
        # 处理每个设备的详细信息
        for device in devices:
            status = device.status
            ip = device.ip
            name = device.name or ""
            remark = device.remark or ""
            dtype = device.type or "未分类"
            
            if status and status.is_online:
                # 设备在线
                try:
                    # 计算在线时长
                    last_seen = status.last_seen
                    start_time = status.start_time or last_seen
                    
                    delta = int(now_ts - start_time.timestamp())
                    h, rem = divmod(delta, 3600)
                    m, s = divmod(rem, 60)
                    duration = f"{h:02d}:{m:02d}:{s:02d}"
                    
                    # 计算最后一次检测的时间差（分钟）
                    last_check_mins = int((now_ts - last_seen.timestamp()) / 60)
                    
                    online[ip] = {
                        "name": name,
                        "remark": remark,
                        "type": dtype,
                        "last_seen": last_seen.strftime("%Y-%m-%d %H:%M:%S"),
                        "last_check_mins": last_check_mins,
                        "duration": duration,
                        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "response_time": status.response_time
                    }
                except Exception as e:
                    logger.error(f"处理设备 {ip} 时间数据出错: {str(e)}")
                    offline[ip] = {"name": name, "remark": remark, "type": dtype, "error": "时间数据格式错误"}
            else:
                # 设备离线
                offline[ip] = {"name": name, "remark": remark, "type": dtype}
        
        # 添加网段信息
        networks = session.query(Network).all()
        network_info = []
        for network in networks:
            network_info.append({
                "id": network.id,
                "name": network.name,
                "cidr": network.cidr,
                "is_active": network.is_active,
                "scan_interval": network.scan_interval,
                "last_scan": network.last_scan.strftime("%Y-%m-%d %H:%M:%S") if network.last_scan else None
            })
        
        return jsonify({
            "online": online,
            "offline": offline,
            "stats": stats,
            "networks": network_info,
            "now": now_str,
            "server_time": now_str,
            "last_update": last_update
        })
    
    except Exception as e:
        logger.error(f"获取状态信息失败: {str(e)}")
        return jsonify({"error": str(e)}), 500
    
    finally:
        session.close()

@app.route("/api/scan", methods=["POST"])
def api_scan():
    try:
        # 使用新的网段扫描器
        network_scanner.scan_all_networks()
        
        # 兼容旧版本
        scanner.check_online_devices()
        
        return api_status()
    except Exception as e:
        logger.error(f"扫描失败: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device", methods=["POST"])
def api_device():
    data = request.get_json() or {}
    ip = data.get("ip")
    
    if not ip:
        return jsonify({"success": False, "error": "缺少IP参数"}), 400
    
    session = get_db_session()
    try:
        # 查找设备
        device = session.query(Device).filter_by(ip=ip).first()
        
        if not device:
            # 如果数据库中不存在，尝试从旧文件中查找
            devices = load_json(DEVICES_FILE, {})
            if ip in devices:
                # 从旧文件导入到数据库
                info = devices[ip]
                device = Device(
                    ip=ip,
                    name=info.get("name", ""),
                    remark=info.get("remark", ""),
                    type=info.get("type", "未分类"),
                    first_seen=datetime.now()
                )
                session.add(device)
                session.commit()
                logger.info(f"从旧文件导入设备: {ip}")
            else:
                # 创建新设备
                device = Device(ip=ip, first_seen=datetime.now())
                session.add(device)
                session.commit()
                logger.info(f"创建新设备: {ip}")
        
        # 动态修改
        modified = False
        for field in ("name", "remark", "type"):
            if field in data:
                setattr(device, field, data[field])
                modified = True
        
        if modified:
            device.last_modified = datetime.now()
            session.commit()
            logger.info(f"更新设备信息: {ip}")
        
        # 同时更新旧文件以保持兼容性
        try:
            devices = load_json(DEVICES_FILE, {})
            if ip not in devices:
                devices[ip] = {}
            
            for field in ("name", "remark", "type"):
                if field in data:
                    devices[ip][field] = data[field]
            
            save_json(DEVICES_FILE, devices)
        except Exception as e:
            logger.warning(f"更新旧文件失败: {str(e)}")
        
        return jsonify({"success": True})
    
    except Exception as e:
        session.rollback()
        logger.error(f"更新设备信息失败: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
    
    finally:
        session.close()

@app.route("/api/history/<ip>")
def api_history(ip):
    period = request.args.get("period", "daily")
    now = datetime.now()
    
    # 确定时间范围
    if period == "daily":
        cutoff = now - timedelta(days=1)
    elif period == "weekly":
        cutoff = now - timedelta(weeks=1)
    elif period == "monthly":
        cutoff = now - timedelta(days=30)
    else:
        cutoff = now - timedelta(days=1)
    
    session = get_db_session()
    try:
        # 查找设备
        device = session.query(Device).filter_by(ip=ip).first()
        
        if not device:
            # 如果数据库中不存在，尝试从旧文件中查找
            old_history = load_json(HISTORY_FILE, [])
            result = []
            
            for entry in old_history:
                ts = datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff:
                    result.append({
                        "timestamp": entry["timestamp"],
                        "online": ip in entry["online"]
                    })
            
            return jsonify(result)
        
        # 查询设备历史记录
        history_records = session.query(DeviceHistory)\
            .filter(DeviceHistory.device_id == device.id)\
            .filter(DeviceHistory.timestamp >= cutoff)\
            .order_by(DeviceHistory.timestamp)\
            .all()
        
        result = []
        for record in history_records:
            result.append({
                "timestamp": record.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "online": record.is_online,
                "response_time": record.response_time
            })
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"获取历史记录失败: {str(e)}")
        return jsonify({"error": str(e)}), 500
    
    finally:
        session.close()

if __name__ == "__main__":
    # 初始化数据库
    init_db()
    
    # 导入旧数据
    try:
        network_scanner.import_legacy_data()
        network_scanner.init_networks()
        logger.info("旧数据导入完成")
    except Exception as e:
        logger.error(f"旧数据导入失败: {str(e)}")
    
    # 启动网段扫描器（后台线程）
    network_scanner.start_scan_loop()
    
    # 兼容旧版本扫描器（降低频率）
    scanner.start_loop()
    
    # 启动Web服务器
    app.run(host="0.0.0.0", port=5000, debug=True)

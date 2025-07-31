import ipaddress
import time
import threading
import os
import logging
from datetime import datetime, timedelta
from ping3 import ping
import subprocess
import socket
from models import Device, DeviceStatus, DeviceHistory, Network, ScanLog, get_db_session
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('network_scanner')

# 获取配置
SCAN_INTERVAL = int(os.environ.get('SCAN_INTERVAL', 30))
HISTORY_RETENTION_DAYS = int(os.environ.get('HISTORY_RETENTION_DAYS', 30))
NETWORK_SEGMENTS = os.environ.get('NETWORK_SEGMENTS', '192.168.1.0/24').split(',')

# 尝试通过 ping 判断 IP 是否在线
def is_online(ip, timeout=1, retries=2):
    response_time = None
    
    # 尝试使用ping3库
    for _ in range(retries):
        try:
            result = ping(ip, timeout=timeout)
            if result is not None and result is not False:
                response_time = result * 1000  # 转换为毫秒
                return True, response_time
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
            start_time = time.time()
            res = subprocess.run(
                ping_params,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True
            )
            end_time = time.time()
            
            if res.returncode == 0:
                response_time = (end_time - start_time) * 1000  # 转换为毫秒
                return True, response_time
        except Exception:
            pass
    
    return False, None

# 尝试获取主机名
def get_hostname(ip, timeout=1):
    try:
        return socket.getfqdn(ip)
    except:
        return None

# 扫描单个网段
def scan_network(network_cidr, session=None):
    if session is None:
        session = get_db_session()
    
    # 查找或创建网络记录
    network = session.query(Network).filter_by(cidr=network_cidr).first()
    if not network:
        network = Network(name=f"网段 {network_cidr}", cidr=network_cidr)
        session.add(network)
        session.commit()
    
    # 创建扫描日志
    scan_log = ScanLog(network_id=network.id, timestamp=datetime.now())
    session.add(scan_log)
    
    start_time = time.time()
    devices_total = 0
    devices_online = 0
    errors = []
    
    try:
        # 解析网段
        network_obj = ipaddress.ip_network(network_cidr)
        devices_total = network_obj.num_addresses - 2  # 减去网络地址和广播地址
        
        # 更新扫描日志
        scan_log.devices_total = devices_total
        session.commit()
        
        # 扫描每个IP
        for ip in network_obj.hosts():
            ip_str = str(ip)
            
            try:
                # 检查设备是否在线
                online, response_time = is_online(ip_str, timeout=1.5, retries=2)
                
                # 查找或创建设备记录
                device = session.query(Device).filter_by(ip=ip_str).first()
                if online:
                    # 设备在线
                    devices_online += 1
                    now = datetime.now()
                    
                    if not device:
                        # 新设备，尝试获取主机名
                        hostname = get_hostname(ip_str)
                        device = Device(ip=ip_str, hostname=hostname, first_seen=now)
                        session.add(device)
                        session.flush()  # 获取ID但不提交
                    
                    # 更新或创建设备状态
                    status = session.query(DeviceStatus).filter_by(device_id=device.id).first()
                    if not status:
                        status = DeviceStatus(device_id=device.id, is_online=True, last_seen=now, start_time=now, response_time=response_time, last_check=now)
                        session.add(status)
                    else:
                        # 如果设备之前离线，现在上线，更新开始时间
                        if not status.is_online:
                            status.start_time = now
                            logger.info(f"设备上线: {ip_str} ({device.name or device.hostname or '未知设备'})")
                        
                        status.is_online = True
                        status.last_seen = now
                        status.response_time = response_time
                        status.last_check = now
                    
                    # 添加历史记录
                    history = DeviceHistory(device_id=device.id, timestamp=now, is_online=True, response_time=response_time)
                    session.add(history)
                else:
                    # 设备离线
                    if device:
                        # 更新设备状态
                        status = session.query(DeviceStatus).filter_by(device_id=device.id).first()
                        if status:
                            # 如果设备之前在线，现在离线，记录日志
                            if status.is_online:
                                logger.info(f"设备离线: {ip_str} ({device.name or device.hostname or '未知设备'})")
                            
                            status.is_online = False
                            status.last_check = datetime.now()
                            
                            # 添加历史记录
                            history = DeviceHistory(device_id=device.id, timestamp=datetime.now(), is_online=False)
                            session.add(history)
                
                # 每10个IP提交一次，减少数据库压力
                if devices_online % 10 == 0:
                    session.commit()
                    
            except Exception as e:
                errors.append(f"{ip_str}: {str(e)}")
                logger.warning(f"扫描IP {ip_str} 时出错: {str(e)}")
        
        # 提交所有更改
        session.commit()
        
    except Exception as e:
        errors.append(f"扫描网段 {network_cidr} 失败: {str(e)}")
        logger.error(f"扫描网段 {network_cidr} 失败: {str(e)}")
    
    # 更新扫描日志
    end_time = time.time()
    scan_duration = end_time - start_time
    
    scan_log.duration = scan_duration
    scan_log.devices_online = devices_online
    if errors:
        scan_log.error_message = '; '.join(errors[:3]) + ('...' if len(errors) > 3 else '')
    
    # 更新网络最后扫描时间
    network.last_scan = datetime.now()
    
    session.commit()
    
    logger.info(f"扫描完成: 网段 {network_cidr}, 总设备 {devices_total}, 在线 {devices_online}, 用时 {scan_duration:.2f}秒")
    
    if errors:
        logger.warning(f"扫描过程中有{len(errors)}个错误: {', '.join(errors[:3])}{'...' if len(errors) > 3 else ''}")
    
    return devices_online

# 清理历史数据
def cleanup_history():
    session = get_db_session()
    try:
        cutoff_date = datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)
        deleted = session.query(DeviceHistory).filter(DeviceHistory.timestamp < cutoff_date).delete()
        session.commit()
        if deleted > 0:
            logger.info(f"已清理 {deleted} 条历史记录 (超过 {HISTORY_RETENTION_DAYS} 天)")
    except Exception as e:
        logger.error(f"清理历史记录失败: {str(e)}")
        session.rollback()
    finally:
        session.close()

# 扫描所有配置的网段
def scan_all_networks():
    session = get_db_session()
    total_online = 0
    
    try:
        # 扫描每个网段
        for network_cidr in NETWORK_SEGMENTS:
            network_cidr = network_cidr.strip()
            if network_cidr:
                try:
                    online_count = scan_network(network_cidr, session)
                    total_online += online_count
                except Exception as e:
                    logger.error(f"扫描网段 {network_cidr} 时出错: {str(e)}")
        
        # 每天清理一次历史数据
        if datetime.now().hour == 3:  # 凌晨3点
            cleanup_history()
            
    except Exception as e:
        logger.error(f"扫描过程中发生错误: {str(e)}")
    finally:
        session.close()
    
    return total_online

# 启动扫描循环
def start_scan_loop(interval=SCAN_INTERVAL):
    def loop():
        consecutive_errors = 0
        last_success = time.time()
        
        while True:
            try:
                start_time = time.time()
                total_online = scan_all_networks()
                end_time = time.time()
                
                scan_duration = end_time - start_time
                logger.info(f"全网扫描完成: 在线设备 {total_online}, 用时 {scan_duration:.2f}秒")
                
                # 重置错误计数
                consecutive_errors = 0
                last_success = time.time()
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"扫描错误 ({consecutive_errors}连续): {str(e)}")
                
                # 如果连续错误过多且间隔时间较长，尝试恢复
                if consecutive_errors >= 5 and time.time() - last_success > 300:  # 5分钟
                    logger.warning(f"检测到连续{consecutive_errors}次错误，尝试恢复...")
                    try:
                        # 尝试重新连接数据库
                        session = get_db_session()
                        session.execute("SELECT 1")
                        session.close()
                        logger.info("数据库连接检查正常")
                    except Exception as recovery_error:
                        logger.error(f"恢复尝试失败: {str(recovery_error)}")
            
            # 动态调整扫描间隔
            actual_interval = interval
            if consecutive_errors > 0:
                # 出错时略微增加间隔
                actual_interval = min(interval * 1.5, 120)  # 最多2分钟
            
            time.sleep(actual_interval)
    
    logger.info(f"启动网络扫描线程，间隔 {interval} 秒")
    threading.Thread(target=loop, daemon=True, name="NetworkScanner").start()

# 导入旧数据到数据库
def import_legacy_data():
    from scanner import load_json
    
    session = get_db_session()
    
    try:
        # 导入设备信息
        devices_data = load_json("devices.json")
        for ip, info in devices_data.items():
            device = session.query(Device).filter_by(ip=ip).first()
            if not device:
                device = Device(
                    ip=ip,
                    name=info.get("name", ""),
                    remark=info.get("remark", ""),
                    type=info.get("type", "未分类"),
                    first_seen=datetime.now()
                )
                session.add(device)
            else:
                device.name = info.get("name", device.name)
                device.remark = info.get("remark", device.remark)
                device.type = info.get("type", device.type)
        
        session.commit()
        logger.info(f"已导入 {len(devices_data)} 个设备信息")
        
        # 导入状态信息
        status_data = load_json("status.json")
        for ip, info in status_data.items():
            device = session.query(Device).filter_by(ip=ip).first()
            if device:
                status = session.query(DeviceStatus).filter_by(device_id=device.id).first()
                
                last_seen = datetime.strptime(info.get("last_seen", datetime.now().strftime("%Y-%m-%d %H:%M:%S")), "%Y-%m-%d %H:%M:%S")
                start_time = datetime.strptime(info.get("start_time", last_seen.strftime("%Y-%m-%d %H:%M:%S")), "%Y-%m-%d %H:%M:%S")
                
                if not status:
                    status = DeviceStatus(
                        device_id=device.id,
                        is_online=True,
                        last_seen=last_seen,
                        start_time=start_time,
                        last_check=datetime.now()
                    )
                    session.add(status)
                else:
                    status.is_online = True
                    status.last_seen = last_seen
                    status.start_time = start_time
                    status.last_check = datetime.now()
        
        session.commit()
        logger.info(f"已导入 {len(status_data)} 个设备状态信息")
        
        # 导入历史记录
        history_data = load_json("history.json")
        history_count = 0
        
        for entry in history_data:
            timestamp = datetime.strptime(entry.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
            online_ips = entry.get("online", [])
            
            for ip in online_ips:
                device = session.query(Device).filter_by(ip=ip).first()
                if device:
                    history = DeviceHistory(
                        device_id=device.id,
                        timestamp=timestamp,
                        is_online=True
                    )
                    session.add(history)
                    history_count += 1
            
            # 每100条提交一次
            if history_count % 100 == 0:
                session.commit()
        
        session.commit()
        logger.info(f"已导入 {history_count} 条历史记录")
        
        return True
    except Exception as e:
        logger.error(f"导入旧数据失败: {str(e)}")
        session.rollback()
        return False
    finally:
        session.close()

# 初始化网络配置
def init_networks():
    session = get_db_session()
    
    try:
        # 检查是否已有网络配置
        existing = session.query(Network).count()
        if existing > 0:
            return
        
        # 添加配置的网段
        for network_cidr in NETWORK_SEGMENTS:
            network_cidr = network_cidr.strip()
            if network_cidr:
                network = Network(
                    name=f"网段 {network_cidr}",
                    cidr=network_cidr,
                    is_active=True,
                    scan_interval=SCAN_INTERVAL
                )
                session.add(network)
        
        session.commit()
        logger.info(f"已初始化 {len(NETWORK_SEGMENTS)} 个网段配置")
    except Exception as e:
        logger.error(f"初始化网络配置失败: {str(e)}")
        session.rollback()
    finally:
        session.close()

# 主函数
def main():
    # 初始化数据库
    from models import init_db
    init_db()
    
    # 初始化网络配置
    init_networks()
    
    # 导入旧数据
    import_legacy_data()
    
    # 启动扫描
    start_scan_loop()

if __name__ == "__main__":
    main()
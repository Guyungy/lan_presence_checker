from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, create_engine, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import os
from datetime import datetime

# 创建基类
Base = declarative_base()

# 设备表
class Device(Base):
    __tablename__ = 'devices'
    
    id = Column(Integer, primary_key=True)
    ip = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=True)
    remark = Column(String(255), nullable=True)
    type = Column(String(50), nullable=True)
    mac_address = Column(String(50), nullable=True)
    hostname = Column(String(100), nullable=True)
    first_seen = Column(DateTime, default=datetime.now)
    last_modified = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    status = relationship("DeviceStatus", back_populates="device", uselist=False, cascade="all, delete-orphan")
    history = relationship("DeviceHistory", back_populates="device", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Device(ip='{self.ip}', name='{self.name}')>"

# 设备状态表
class DeviceStatus(Base):
    __tablename__ = 'device_status'
    
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id'), unique=True)
    is_online = Column(Boolean, default=False)
    last_seen = Column(DateTime, nullable=True)
    start_time = Column(DateTime, nullable=True)  # 本次在线开始时间
    response_time = Column(Float, nullable=True)  # 响应时间(ms)
    last_check = Column(DateTime, default=datetime.now)  # 最后检查时间
    
    # 关系
    device = relationship("Device", back_populates="status")
    
    def __repr__(self):
        status = "在线" if self.is_online else "离线"
        return f"<DeviceStatus(device='{self.device.ip}', status='{status}')>"

# 设备历史记录表
class DeviceHistory(Base):
    __tablename__ = 'device_history'
    
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey('devices.id'))
    timestamp = Column(DateTime, default=datetime.now, index=True)
    is_online = Column(Boolean, default=False)
    response_time = Column(Float, nullable=True)  # 响应时间(ms)
    
    # 关系
    device = relationship("Device", back_populates="history")
    
    def __repr__(self):
        status = "在线" if self.is_online else "离线"
        return f"<DeviceHistory(device='{self.device.ip}', timestamp='{self.timestamp}', status='{status}')>"

# 网段表
class Network(Base):
    __tablename__ = 'networks'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    cidr = Column(String(50), nullable=False)  # 例如 192.168.1.0/24
    is_active = Column(Boolean, default=True)
    scan_interval = Column(Integer, default=30)  # 扫描间隔(秒)
    created_at = Column(DateTime, default=datetime.now)
    last_scan = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<Network(name='{self.name}', cidr='{self.cidr}')>"

# 扫描日志表
class ScanLog(Base):
    __tablename__ = 'scan_logs'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    network_id = Column(Integer, ForeignKey('networks.id'), nullable=True)
    duration = Column(Float, nullable=True)  # 扫描用时(秒)
    devices_total = Column(Integer, default=0)
    devices_online = Column(Integer, default=0)
    error_message = Column(String(255), nullable=True)
    
    def __repr__(self):
        return f"<ScanLog(timestamp='{self.timestamp}', devices_online={self.devices_online})>"

# 数据库连接
def get_db_session():
    # 默认使用SQLite，可通过环境变量配置
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///presence.db')
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

# 初始化数据库
def init_db():
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///presence.db')
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    return engine
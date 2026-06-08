#!/usr/bin/env python3
"""
SSL Certificate Agent - 内网证书采集端
部署在内网，定时采集 SSL 证书信息

支持三种模式：
1. 推送模式（push，默认）：Agent 主动向 Server 推送指标和心跳，不需要暴露端口
2. 拉取模式（pull）：Agent 暴露 HTTP API 供 Server 拉取数据
3. 双模式（dual）：同时暴露端口供 Server 拉取，又主动推送数据到 Server（推荐）

功能：
1. 定时检测 SSL 证书
2. 主动推送指标数据到 Server（push/dual 模式）
3. 定期发送心跳到 Server（push/dual 模式）
4. 暴露 HTTP API 供 Server 拉取（pull/dual 模式）
5. 从 Server 同步目标配置
6. 离线缓存，网络恢复后自动补推
"""

import json
import ssl
import socket
import time
import os
import sys
import logging
import threading
import datetime
from urllib.parse import urlparse
from queue import Queue
from flask import Flask, jsonify, request
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Flask 应用（pull/dual 模式下启动 HTTP 服务）
app = Flask(__name__)

# 默认配置
DEFAULT_CONFIG = {
    'scrape_interval': 180,
    'timeout': 30,
    'retry_interval': 30,
    'heartbeat_interval': 60,
    'max_retries': 3,
    'offline_cache_size': 1000,
    'listen_host': '0.0.0.0',
    'listen_port': 8091,
    'server_url': '',  # Server 地址，用于推送数据和同步目标配置
    'sync_interval': 300,  # 目标同步间隔（秒）
    'enable_https': False,  # Agent 是否启用 HTTPS
    'verify_ssl': True,  # 是否验证 Server SSL 证书
    'push_mode': True,  # 推送模式（默认启用，Agent 不需要暴露端口）
    'push_interval': 30,  # 指标推送间隔（秒），检测完成后也会立即推送
    'heartbeat_timeout': 300,  # Server 端判断 Agent 离线的心跳超时（秒）
}

# 运行模式: push / pull / dual
AGENT_MODE = 'push'  # 默认推送模式

# 全局变量
AGENT_CONFIG = None
METRICS_BUFFER = []
PUSH_QUEUE = []  # 待推送的指标队列
METRICS_LOCK = threading.Lock()
PUSH_LOCK = threading.Lock()
SCRAPE_INTERVAL = 180
TARGETS_LAST_SYNC = None  # 上次同步时间
LAST_PUSH_TIME = None  # 上次推送时间


def _resolve_agent_mode():
    """根据 AGENT_MODE 和 push_mode 配置解析实际运行模式
    支持三种模式：
    - push:  仅推送，不暴露端口
    - pull:  仅拉取，暴露端口，不推送
    - dual:  同时暴露端口+推送（推荐，兼顾两种架构）
    """
    global AGENT_MODE
    
    mode = os.getenv('AGENT_MODE', '').lower()
    
    if mode in ('push', 'pull', 'dual'):
        AGENT_MODE = mode
    else:
        # 兼容旧的 AGENT_PUSH_MODE 环境变量
        push_mode = os.getenv('AGENT_PUSH_MODE', '').lower()
        if push_mode == 'true':
            AGENT_MODE = 'push'
        elif push_mode == 'false':
            AGENT_MODE = 'pull'
        else:
            # 使用默认配置中的 push_mode
            AGENT_MODE = 'push' if DEFAULT_CONFIG['push_mode'] else 'pull'
    
    return AGENT_MODE


def _load_config(config_path=None):
    """加载配置文件"""
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            logger.info(f"从 {config_path} 加载配置")
            # 补充环境变量配置
            config['server_url'] = os.getenv('SERVER_URL', config.get('server_url', ''))
            config['sync_interval'] = int(os.getenv('SYNC_INTERVAL', config.get('sync_interval', DEFAULT_CONFIG['sync_interval'])))
            # 模式配置: 优先使用 AGENT_MODE，其次兼容 AGENT_PUSH_MODE
            mode_from_env = os.getenv('AGENT_MODE', '').lower()
            if mode_from_env in ('push', 'pull', 'dual'):
                config['agent_mode'] = mode_from_env
            else:
                config['push_mode'] = os.getenv('AGENT_PUSH_MODE', str(config.get('push_mode', DEFAULT_CONFIG['push_mode']))).lower() == 'true'
            config['push_interval'] = int(os.getenv('PUSH_INTERVAL', config.get('push_interval', DEFAULT_CONFIG['push_interval'])))
            # HTTPS 配置
            config['enable_https'] = os.getenv('AGENT_ENABLE_HTTPS', str(config.get('enable_https', DEFAULT_CONFIG['enable_https']))).lower() == 'true'
            config['verify_ssl'] = os.getenv('AGENT_VERIFY_SSL', str(config.get('verify_ssl', DEFAULT_CONFIG['verify_ssl']))).lower() == 'true'
            config['targets'] = []  # 不再从本地文件读取，启动后由 Server 实时下发
            return config
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    
    # 从环境变量加载
    config = dict(DEFAULT_CONFIG)
    config['scrape_interval'] = int(os.getenv('SCRAPE_INTERVAL', DEFAULT_CONFIG['scrape_interval']))
    config['timeout'] = int(os.getenv('SCRAPE_TIMEOUT', DEFAULT_CONFIG['timeout']))
    config['listen_host'] = os.getenv('AGENT_LISTEN_HOST', DEFAULT_CONFIG['listen_host'])
    config['listen_port'] = int(os.getenv('AGENT_LISTEN_PORT', DEFAULT_CONFIG['listen_port']))
    config['server_url'] = os.getenv('SERVER_URL', '')  # Server 地址
    config['sync_interval'] = int(os.getenv('SYNC_INTERVAL', DEFAULT_CONFIG['sync_interval']))
    # 模式配置: 优先使用 AGENT_MODE，其次兼容 AGENT_PUSH_MODE
    mode_from_env = os.getenv('AGENT_MODE', '').lower()
    if mode_from_env in ('push', 'pull', 'dual'):
        config['agent_mode'] = mode_from_env
    else:
        config['push_mode'] = os.getenv('AGENT_PUSH_MODE', str(DEFAULT_CONFIG['push_mode'])).lower() == 'true'
    config['push_interval'] = int(os.getenv('PUSH_INTERVAL', DEFAULT_CONFIG['push_interval']))
    # HTTPS 配置
    config['enable_https'] = os.getenv('AGENT_ENABLE_HTTPS', str(DEFAULT_CONFIG['enable_https'])).lower() == 'true'
    config['verify_ssl'] = os.getenv('AGENT_VERIFY_SSL', str(DEFAULT_CONFIG['verify_ssl'])).lower() == 'true'
    config['targets'] = []  # 不再从本地文件读取，启动后由 Server 实时下发
    
    return config


def _sync_targets_from_server():
    """从 Server 同步目标配置"""
    global TARGETS_LAST_SYNC, AGENT_CONFIG
    
    server_url = AGENT_CONFIG.get('server_url', '')
    if not server_url:
        logger.debug("未配置 SERVER_URL，跳过目标同步")
        return
    
    # 根据配置决定是否使用 HTTPS
    enable_https = AGENT_CONFIG.get('enable_https', False)
    verify_ssl = AGENT_CONFIG.get('verify_ssl', True)
    
    # 如果启用 HTTPS，确保 URL 使用 https://
    if enable_https and server_url.startswith('http://'):
        server_url = server_url.replace('http://', 'https://')
    
    # 从 Server 获取分配给本 Agent 的目标
    sys_info = _get_system_info()
    agent_id = os.getenv('AGENT_ID', '')
    
    # 构建 Agent 标识信息
    url = f"{server_url}/api/v1/agents/targets"
    params = {
        'agent_id': agent_id,
        'hostname': sys_info.get('hostname', ''),
        'ip': sys_info.get('ip', '')
    }
    
    try:
        logger.info(f"从 Server 同步目标配置: {url} (verify_ssl={verify_ssl})")
        logger.info(f"Agent 标识 - agent_id: {agent_id}, hostname: {sys_info.get('hostname')}, ip: {sys_info.get('ip')}")
        
        resp = requests.get(url, params=params, timeout=30, verify=verify_ssl)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                targets = data.get('targets', [])
                
                # 更新配置（仅内存，不再写本地文件）
                AGENT_CONFIG['targets'] = targets
                
                TARGETS_LAST_SYNC = datetime.datetime.now()
                logger.info(f"成功从 Server 同步 {len(targets)} 个目标配置")
            else:
                logger.warning(f"Server 返回错误: {data.get('message', '未知错误')}")
        else:
            logger.warning(f"Server 请求失败: HTTP {resp.status_code}")
            
    except requests.exceptions.ConnectionError:
        logger.warning("无法连接到 Server，使用本地缓存配置")
    except requests.exceptions.Timeout:
        logger.warning("Server 连接超时，使用本地缓存配置")
    except Exception as e:
        logger.error(f"同步目标配置失败: {e}")


def _get_system_info():
    """获取系统信息 - 增强版，支持容器环境"""
    import subprocess
    
    # 1. 获取主机名 - 优先使用环境变量，其次 socket
    hostname = os.getenv('AGENT_HOSTNAME', '')
    if not hostname or hostname == 'unknown':
        try:
            hostname = socket.gethostname()
        except:
            hostname = 'unknown'
    
    # 2. 获取 IP 地址 - 多策略尝试
    ip = os.getenv('AGENT_IP', '')
    
    if not ip or ip == 'unknown':
        # 策略1: 尝试连接外部地址获取本机出口 IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            # 连接一个公共 DNS 服务器
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
        except:
            pass
    
    if not ip or ip == 'unknown':
        # 策略2: 通过 hostname 解析
        try:
            ip = socket.gethostbyname(hostname)
        except:
            pass
    
    if not ip or ip == 'unknown':
        # 策略3: 遍历网卡获取非回环地址
        try:
            import netifaces
            for interface in netifaces.interfaces():
                if interface.startswith('lo'):
                    continue
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        addr = addr_info.get('addr')
                        if addr and not addr.startswith('127.'):
                            ip = addr
                            break
                if ip and ip != 'unknown':
                    break
        except ImportError:
            # 如果没有 netifaces，使用 ifconfig/ip 命令
            try:
                result = subprocess.run(['ip', 'route', 'get', '1'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # 从输出中提取 src 后面的 IP
                    for line in result.stdout.split('\n'):
                        if 'src' in line:
                            parts = line.split()
                            for i, part in enumerate(parts):
                                if part == 'src' and i + 1 < len(parts):
                                    ip = parts[i + 1]
                                    break
                if not ip or ip == 'unknown':
                    result = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        import re
                        inet_pattern = re.compile(r'inet\s+(\d+\.\d+\.\d+\.\d+)')
                        for match in inet_pattern.finditer(result.stdout):
                            addr = match.group(1)
                            if not addr.startswith('127.'):
                                ip = addr
                                break
            except:
                pass
    
    if not ip or ip == 'unknown':
        ip = '127.0.0.1'
    
    return {
        'hostname': hostname,
        'ip': ip,
        'version': '2.0',
        'platform': sys.platform
    }


def _parse_target_url(url):
    """解析目标 URL"""
    result = {
        'host': None,
        'port': 443,
        'is_https': True
    }
    
    if '://' not in url:
        url = 'https://' + url
    
    parsed = urlparse(url)
    host_port = parsed.netloc or parsed.path
    
    if ':' in host_port:
        result['host'], port_str = host_port.rsplit(':', 1)
        result['port'] = int(port_str)
    else:
        result['host'] = host_port
        result['port'] = 443 if parsed.scheme == 'https' else 80
    
    result['is_https'] = parsed.scheme == 'https'
    
    return result


def _check_cert(target):
    """检测单个目标的 SSL 证书"""
    url = target.get('url', '')
    timeout = target.get('timeout', AGENT_CONFIG.get('timeout', 30))
    
    parsed = _parse_target_url(url)
    host = parsed['host']
    port = parsed['port']
    
    metrics = []
    
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        logger.info(f"检测证书: {host}:{port}")
        
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                
                if not cert_der:
                    metrics.append(_create_metric('ssl_cert_check_success', 0, target))
                    return metrics
                
                try:
                    from cryptography import x509
                    from cryptography.hazmat.backends import default_backend
                    
                    cert_obj = x509.load_der_x509_certificate(cert_der, default_backend())
                    
                    not_after = cert_obj.not_valid_after_utc
                    not_before = cert_obj.not_valid_before_utc
                    now = datetime.datetime.now(datetime.timezone.utc)
                    
                    time_diff = (not_after.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds()
                    days_left = round(time_diff / 86400, 1)
                    
                    # 提取证书详细信息
                    subject = {attr.oid._name: attr.value for attr in cert_obj.subject}
                    issuer = {attr.oid._name: attr.value for attr in cert_obj.issuer}
                    subject_cn = subject.get('commonName', '')
                    issuer_cn = issuer.get('commonName', '')
                    issuer_org = issuer.get('organizationName', issuer_cn)
                    serial = format(cert_obj.serial_number, 'X')
                    version = str(cert_obj.version.value)
                    
                    # 提取 SANs
                    sans = []
                    try:
                        san_ext = cert_obj.extensions.get_extension_for_oid(x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                        sans = [name.value for name in san_ext.value]
                    except:
                        pass
                    
                    base_labels = {
                        'hostname': host,
                        'port': str(port),
                        'service_name': target.get('service_name', url),
                        'owner': target.get('owner', 'unknown'),
                        'owner_email': target.get('owner_email', ''),
                        'env': target.get('env', 'production')
                    }
                    
                    # 带详细信息的标签
                    detail_labels = {
                        **base_labels,
                        'subject_cn': subject_cn,
                        'issuer_cn': issuer_cn,
                        'issuer_org': issuer_org,
                        'subject': json.dumps(subject),
                        'issuer': json.dumps(issuer)
                    }
                    
                    # 基础指标
                    metrics.append({
                        'metric_name': 'ssl_cert_check_success',
                        'metric_type': 'ssl_cert_check_success',
                        'value': 1,
                        **base_labels
                    })
                    
                    metrics.append({
                        'metric_name': 'ssl_cert_days_left',
                        'metric_type': 'ssl_cert_days_left',
                        'value': days_left,
                        **base_labels
                    })
                    
                    metrics.append({
                        'metric_name': 'ssl_cert_not_after_timestamp',
                        'metric_type': 'ssl_cert_not_after_timestamp',
                        'value': int(not_after.timestamp()),
                        **base_labels
                    })
                    
                    # WebTrust 检测
                    is_webtrust = _is_webtrust_ca(issuer_org)
                    metrics.append({
                        'metric_name': 'ssl_cert_is_webtrust',
                        'metric_type': 'ssl_cert_is_webtrust',
                        'value': 1 if is_webtrust else 0,
                        **detail_labels
                    })
                    
                    # SANs 数量
                    metrics.append({
                        'metric_name': 'ssl_cert_sans_count',
                        'metric_type': 'ssl_cert_sans_count',
                        'value': len(sans),
                        **detail_labels
                    })
                    
                    # 序列号
                    metrics.append({
                        'metric_name': 'ssl_cert_serial',
                        'metric_type': 'ssl_cert_serial',
                        'value': 1,
                        **{**detail_labels, 'serial': serial}
                    })
                    
                    logger.info(f"证书检测成功: {host}:{port}, 剩余 {days_left} 天, 颁发者: {issuer_org}")
                    
                except ImportError:
                    cert = ssock.getpeercert()
                    if cert and 'notAfter' in cert:
                        not_after = datetime.datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
                        now = datetime.datetime.now()
                        days_left = (not_after.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds() / 86400
                        
                        metrics.append(_create_metric('ssl_cert_check_success', 1, target))
                        metrics.append({
                            'metric_name': 'ssl_cert_days_left',
                            'metric_type': 'ssl_cert_days_left',
                            'value': round(days_left, 1),
                            'hostname': host,
                            'port': str(port),
                            'service_name': target.get('service_name', url),
                            'owner': target.get('owner', 'unknown'),
                            'env': target.get('env', 'production')
                        })
    
    except socket.timeout:
        logger.warning(f"连接超时: {host}:{port}")
        metrics.append(_create_metric('ssl_cert_check_success', 0, target))
    except ssl.SSLError as e:
        logger.warning(f"SSL 错误: {host}:{port} - {e}")
        metrics.append(_create_metric('ssl_cert_check_success', 0, target))
    except Exception as e:
        logger.error(f"检测失败: {host}:{port} - {e}")
        metrics.append(_create_metric('ssl_cert_check_success', 0, target))
    
    return metrics


# WebTrust CA 组织名称列表
WEBTRUST_CA_PATTERNS = [
    'DigiCert', 'GlobalSign', "Let's Encrypt", 'ISRG', 'Comodo', 'Sectigo',
    'Entrust', 'Thawte', 'GeoTrust', 'RapidSSL', 'GoDaddy', 'Symantec',
    'DigiCert Inc', 'Google Trust Services', 'Amazon', 'Microsoft', 'TrustAsia',
    'SecureSite', 'Equifax', 'VeriSign', 'GTS', 'Cloudflare', 'ZeroSSL',
    'SSL.com', 'eMudhra', 'Actalis', 'Buypass', 'HARICA', 'Secom',
    'CFCA', 'vTrus', 'WoSign', 'DunTrus', 'CerSign', 'GDCA', 'WoTrus',
    '奇安信', 'H3C', '天威诚信', '中科三方', '沃通', 'Starfield'
]


def _is_webtrust_ca(issuer_org: str) -> bool:
    """判断颁发者是否为 WebTrust 认证的 CA"""
    if not issuer_org:
        return False
    issuer_upper = issuer_org.upper()
    for pattern in WEBTRUST_CA_PATTERNS:
        if pattern.upper() in issuer_upper:
            return True
    return False


def _create_metric(metric_name, value, target):
    """创建指标对象"""
    parsed = _parse_target_url(target.get('url', ''))
    return {
        'metric_name': metric_name,
        'metric_type': metric_name,
        'value': value,
        'hostname': parsed['host'],
        'port': str(parsed['port']),
        'service_name': target.get('service_name', target.get('url', '')),
        'owner': target.get('owner', 'unknown'),
        'owner_email': target.get('owner_email', ''),
        'env': target.get('env', 'production')
    }


def scrape():
    """执行一次证书检测（并发版本）"""
    global METRICS_BUFFER
    
    targets = AGENT_CONFIG.get('targets', [])
    if not targets:
        logger.warning("没有配置监控目标，跳过检测")
        return []
    
    logger.info(f"开始检测 {len(targets)} 个目标（并发）...")
    
    from concurrent.futures import ThreadPoolExecutor, wait
    
    # 并发检测，限制最大并发数
    max_workers = min(len(targets), 10)
    all_metrics = []
    
    def check_target_concurrent(target):
        """并发检测单个目标"""
        try:
            return _check_cert(target)
        except Exception as e:
            logger.error(f"检测目标异常: {target.get('url', '')} - {e}")
            return [_create_metric('ssl_cert_check_success', 0, target)]
    
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {executor.submit(check_target_concurrent, t): t for t in targets}
    
    # 等待所有任务完成，设置整体超时
    done, not_done = wait(futures.keys(), timeout=60)
    
    # 收集已完成的结果
    for future in done:
        try:
            result = future.result()
            if result:
                all_metrics.extend(result)
        except Exception as e:
            logger.error(f"获取结果异常: {e}")
    
    # 取消未完成的任务
    for future in not_done:
        future.cancel()
        target = futures[future]
        logger.warning(f"目标检测超时: {target.get('url', '')}")
    
    # 立即关闭 executor，不等待
    executor.shutdown(wait=False)
    
    # 添加时间戳
    timestamp = datetime.datetime.now().isoformat()
    for m in all_metrics:
        m['timestamp'] = timestamp
    
    # 更新本地缓存
    with METRICS_LOCK:
        METRICS_BUFFER.extend(all_metrics)
        # 保留最近 1000 条
        if len(METRICS_BUFFER) > 1000:
            METRICS_BUFFER = METRICS_BUFFER[-1000:]
    
    # push/dual 模式下，将指标加入推送队列
    if AGENT_MODE in ('push', 'dual'):
        with PUSH_LOCK:
            PUSH_QUEUE.extend(all_metrics)
            # 限制推送队列大小
            if len(PUSH_QUEUE) > 1000:
                PUSH_QUEUE[:] = PUSH_QUEUE[-1000:]
    
    logger.info(f"检测完成，共 {len(all_metrics)} 条指标")
    
    return all_metrics


# ========== 推送模式功能 ==========

def _get_server_url():
    """获取 Server URL（处理 HTTPS）"""
    server_url = AGENT_CONFIG.get('server_url', '')
    if not server_url:
        return None
    
    enable_https = AGENT_CONFIG.get('enable_https', False)
    if enable_https and server_url.startswith('http://'):
        server_url = server_url.replace('http://', 'https://')
    
    return server_url


def push_metrics_to_server():
    """主动推送指标数据到 Server"""
    global LAST_PUSH_TIME
    
    server_url = _get_server_url()
    if not server_url:
        logger.debug("未配置 SERVER_URL，跳过指标推送")
        return False
    
    verify_ssl = AGENT_CONFIG.get('verify_ssl', True)
    agent_id = os.getenv('AGENT_ID', '')
    sys_info = _get_system_info()
    
    # 从推送队列获取待推送的指标
    with PUSH_LOCK:
        metrics_to_push = list(PUSH_QUEUE)
    
    if not metrics_to_push:
        logger.debug("没有待推送的指标")
        return True
    
    url = f"{server_url}/api/v1/agents/{agent_id}/metrics"
    
    payload = {
        'agent_id': agent_id,
        'agent_info': sys_info,
        'metrics': metrics_to_push,
        'count': len(metrics_to_push),
        'agent_mode': AGENT_MODE,
    }
    
    try:
        logger.info(f"推送 {len(metrics_to_push)} 条指标到 Server: {url}")
        resp = requests.post(url, json=payload, timeout=30, verify=verify_ssl)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                # 推送成功，清空已推送的指标
                with PUSH_LOCK:
                    # 只清除已推送的数量（可能期间有新的指标加入）
                    pushed_count = len(metrics_to_push)
                    if len(PUSH_QUEUE) >= pushed_count:
                        PUSH_QUEUE[:] = PUSH_QUEUE[pushed_count:]
                    else:
                        PUSH_QUEUE.clear()
                
                LAST_PUSH_TIME = datetime.datetime.now()
                logger.info(f"成功推送 {len(metrics_to_push)} 条指标到 Server")
                return True
            else:
                logger.warning(f"Server 返回错误: {data.get('message', '未知错误')}")
                return False
        else:
            logger.warning(f"指标推送失败: HTTP {resp.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        logger.warning("无法连接到 Server，指标将缓存在本地等待重试")
        return False
    except requests.exceptions.Timeout:
        logger.warning("Server 连接超时，指标将缓存在本地等待重试")
        return False
    except Exception as e:
        logger.error(f"推送指标失败: {e}")
        return False


def push_heartbeat_to_server():
    """定期向 Server 发送心跳"""
    server_url = _get_server_url()
    if not server_url:
        logger.debug("未配置 SERVER_URL，跳过心跳")
        return False
    
    verify_ssl = AGENT_CONFIG.get('verify_ssl', True)
    agent_id = os.getenv('AGENT_ID', '')
    sys_info = _get_system_info()
    
    url = f"{server_url}/api/v1/agents/{agent_id}/heartbeat"
    
    payload = {
        'agent_id': agent_id,
        'agent_info': sys_info,
        'targets_count': len(AGENT_CONFIG.get('targets', [])),
        'metrics_buffer_size': len(METRICS_BUFFER),
        'push_queue_size': len(PUSH_QUEUE),
        'config': {
            'scrape_interval': AGENT_CONFIG.get('scrape_interval', DEFAULT_CONFIG['scrape_interval']),
            'push_mode': AGENT_MODE in ('push', 'dual'),
            'agent_mode': AGENT_MODE,
        },
        'local_targets': [{'url': t.get('url', ''), 'service_name': t.get('service_name', '')} for t in AGENT_CONFIG.get('targets', [])],
    }
    
    try:
        logger.debug(f"发送心跳到 Server: {url}")
        resp = requests.post(url, json=payload, timeout=10, verify=verify_ssl)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                logger.debug("心跳发送成功")
                return True
            else:
                logger.warning(f"心跳返回错误: {data.get('message', '未知错误')}")
                return False
        else:
            logger.warning(f"心跳发送失败: HTTP {resp.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        logger.debug("无法连接到 Server，心跳发送失败")
        return False
    except requests.exceptions.Timeout:
        logger.debug("Server 心跳连接超时")
        return False
    except Exception as e:
        logger.error(f"心跳发送失败: {e}")
        return False


def _push_loop():
    """推送模式主循环：定时检测 + 推送指标 + 心跳"""
    global SCRAPE_INTERVAL, TARGETS_LAST_SYNC
    
    SCRAPE_INTERVAL = AGENT_CONFIG.get('scrape_interval', DEFAULT_CONFIG['scrape_interval'])
    sync_interval = AGENT_CONFIG.get('sync_interval', DEFAULT_CONFIG['sync_interval'])
    heartbeat_interval = AGENT_CONFIG.get('heartbeat_interval', DEFAULT_CONFIG['heartbeat_interval'])
    push_interval = AGENT_CONFIG.get('push_interval', DEFAULT_CONFIG['push_interval'])
    
    # 启动时先尝试同步目标配置
    _sync_targets_from_server()
    
    # 立即执行一次检测
    scrape()
    
    # 检测后立即推送
    push_metrics_to_server()
    
    # 发送一次心跳
    push_heartbeat_to_server()
    
    last_scrape_time = time.time()
    last_heartbeat_time = time.time()
    last_push_time = time.time()
    last_sync_time = time.time()
    
    while True:
        try:
            time.sleep(5)  # 每5秒检查一次
            now = time.time()
            
            # 1. 定时检测：检测前先从 Server 实时拉取最新目标（不再读本地文件）
            if now - last_scrape_time >= SCRAPE_INTERVAL:
                _sync_targets_from_server()
                scrape()
                last_scrape_time = now
                
                # 检测后立即推送
                push_metrics_to_server()
                last_push_time = now
            
            # 3. 定时推送缓存指标（如果推送队列有数据）
            if now - last_push_time >= push_interval:
                with PUSH_LOCK:
                    has_pending = len(PUSH_QUEUE) > 0
                if has_pending:
                    push_metrics_to_server()
                last_push_time = now
            
            # 4. 定时心跳
            if now - last_heartbeat_time >= heartbeat_interval:
                push_heartbeat_to_server()
                last_heartbeat_time = now
                
        except Exception as e:
            logger.error(f"推送循环异常: {e}")
            time.sleep(10)


# ========== HTTP API 接口（拉取模式兼容） ==========

@app.route('/health')
def health():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': 'ssl-cert-agent',
        'mode': AGENT_MODE,
        'targets_count': len(AGENT_CONFIG.get('targets', [])),
        'metrics_buffer_size': len(METRICS_BUFFER)
    })


@app.route('/info')
def info():
    """Agent 信息"""
    sys_info = _get_system_info()
    return jsonify({
        'status': 'success',
        'agent_info': sys_info,
        'config': {
            'scrape_interval': SCRAPE_INTERVAL,
            'targets_count': len(AGENT_CONFIG.get('targets', [])),
            'agent_mode': AGENT_MODE,
            'push_mode': AGENT_MODE in ('push', 'dual'),
        }
    })


@app.route('/metrics')
def metrics():
    """
    Prometheus 格式的指标数据
    供 Server 拉取使用（拉取模式兼容）
    """
    with METRICS_LOCK:
        current_metrics = list(METRICS_BUFFER)
    
    # 构建 Prometheus 格式输出
    output_lines = []
    
    # 帮助信息
    output_lines.append('# HELP ssl_cert_days_left SSL certificate days left until expiry')
    output_lines.append('# TYPE ssl_cert_days_left gauge')
    output_lines.append('# HELP ssl_cert_not_after_timestamp SSL certificate notAfter timestamp')
    output_lines.append('# TYPE ssl_cert_not_after_timestamp gauge')
    output_lines.append('# HELP ssl_cert_check_success SSL certificate check success (1=success, 0=failure)')
    output_lines.append('# TYPE ssl_cert_check_success gauge')
    output_lines.append('# HELP ssl_cert_is_webtrust SSL certificate is issued by WebTrust certified CA (1=yes, 0=no)')
    output_lines.append('# TYPE ssl_cert_is_webtrust gauge')
    output_lines.append('# HELP ssl_cert_sans_count SSL certificate Subject Alternative Names count')
    output_lines.append('# TYPE ssl_cert_sans_count gauge')
    output_lines.append('# HELP ssl_cert_serial SSL certificate serial number (labels only)')
    output_lines.append('# TYPE ssl_cert_serial gauge')
    
    # 按指标类型分组
    metrics_by_type = {}
    for m in current_metrics:
        metric_type = m.get('metric_type', 'unknown')
        if metric_type not in metrics_by_type:
            metrics_by_type[metric_type] = []
        metrics_by_type[metric_type].append(m)
    
    # 基础标签
    base_label_keys = ['hostname', 'port', 'service_name', 'owner', 'owner_email', 'env']
    
    # 带证书详细信息的标签
    detail_label_keys = ['hostname', 'port', 'service_name', 'owner', 'owner_email', 'env',
                        'subject_cn', 'issuer_cn', 'issuer_org', 'subject', 'issuer']
    
    # 输出 ssl_cert_days_left (基础标签)
    for m in metrics_by_type.get('ssl_cert_days_left', []):
        labels = _build_labels(m, base_label_keys)
        output_lines.append(f'ssl_cert_days_left{{{labels}}} {m.get("value", 0)}')
    
    # 输出 ssl_cert_not_after_timestamp (基础标签)
    for m in metrics_by_type.get('ssl_cert_not_after_timestamp', []):
        labels = _build_labels(m, base_label_keys)
        output_lines.append(f'ssl_cert_not_after_timestamp{{{labels}}} {m.get("value", 0)}')
    
    # 输出 ssl_cert_check_success (基础标签)
    for m in metrics_by_type.get('ssl_cert_check_success', []):
        labels = _build_labels(m, base_label_keys)
        output_lines.append(f'ssl_cert_check_success{{{labels}}} {m.get("value", 0)}')
    
    # 输出 ssl_cert_is_webtrust (带详细信息标签)
    for m in metrics_by_type.get('ssl_cert_is_webtrust', []):
        labels = _build_labels(m, detail_label_keys)
        output_lines.append(f'ssl_cert_is_webtrust{{{labels}}} {m.get("value", 0)}')
    
    # 输出 ssl_cert_sans_count (带详细信息标签)
    for m in metrics_by_type.get('ssl_cert_sans_count', []):
        labels = _build_labels(m, detail_label_keys)
        output_lines.append(f'ssl_cert_sans_count{{{labels}}} {m.get("value", 0)}')
    
    # 输出 ssl_cert_serial (带详细信息标签 + serial)
    for m in metrics_by_type.get('ssl_cert_serial', []):
        labels = _build_labels(m, detail_label_keys + ['serial'])
        output_lines.append(f'ssl_cert_serial{{{labels}}} {m.get("value", 0)}')
    
    # Agent 健康状态指标
    sys_info = _get_system_info()
    labels = f'hostname="{sys_info.get("hostname", "unknown")}",ip="{sys_info.get("ip", "unknown")}"'
    output_lines.append(f'ssl_agent_health{{{labels}}} 1')
    
    return '\n'.join(output_lines) + '\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/api/v1/targets')
def api_targets():
    """
    返回 Agent 本地配置的目标列表
    供 Server 自动发现和同步使用（拉取模式兼容）
    """
    targets = AGENT_CONFIG.get('targets', [])
    sys_info = _get_system_info()
    agent_id = os.getenv('AGENT_ID', '')
    
    # 为每个目标添加 Agent 信息
    enriched_targets = []
    for target in targets:
        enriched_targets.append({
            'id': target.get('id', ''),
            'url': target.get('url', ''),
            'service_name': target.get('service_name', ''),
            'owner': target.get('owner', ''),
            'owner_email': target.get('owner_email', ''),
            'env': target.get('env', 'test'),
            'enabled': target.get('enabled', True),
            'agent_id': agent_id,
            'agent_name': sys_info.get('hostname', agent_id),
            'agent_host': sys_info.get('ip', ''),
            'agent_port': AGENT_CONFIG.get('listen_port', 8091)
        })
    
    return jsonify({
        'status': 'success',
        'agent_id': agent_id,
        'agent_info': sys_info,
        'targets': enriched_targets,
        'count': len(enriched_targets)
    })


@app.route('/api/v1/metrics')
def api_metrics():
    """JSON 格式的指标数据（拉取模式兼容）"""
    with METRICS_LOCK:
        current_metrics = list(METRICS_BUFFER)
    
    sys_info = _get_system_info()
    
    return jsonify({
        'status': 'success',
        'agent_info': sys_info,
        'metrics': current_metrics,
        'count': len(current_metrics),
        'timestamp': datetime.datetime.now().isoformat()
    })


@app.route('/api/v1/check', methods=['POST'])
def api_check_target():
    """
    按需检测单个目标证书（供 Server 代理检测使用）
    请求体: {
        "url": "https://www.google.com",
        "timeout": 30,
        "service_name": "google",
        "owner": "xxx",
        "owner_email": "xx@xx.com",
        "env": "production"
    }
    返回: agent 检测后的 metrics 列表
    """
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'无效的请求体: {e}',
            'metrics': []
        }), 400
    
    url = data.get('url', '')
    if not url:
        return jsonify({
            'status': 'error',
            'message': '缺少 url 参数',
            'metrics': []
        }), 400
    
    # 构造 target 对象传给 _check_cert
    target = {
        'url': url,
        'timeout': data.get('timeout', 30),
        'service_name': data.get('service_name', url),
        'owner': data.get('owner', 'unknown'),
        'owner_email': data.get('owner_email', ''),
        'env': data.get('env', 'production')
    }
    
    logger.info(f"[代理检测] Server 请求检测: {url}")
    metrics = _check_cert(target)
    logger.info(f"[代理检测] {url} 检测完成, 结果数: {len(metrics)}")
    
    # 同时将结果存入本地缓冲区
    with METRICS_LOCK:
        METRICS_BUFFER.extend(metrics)
    
    return jsonify({
        'status': 'success',
        'url': url,
        'metrics': metrics,
        'count': len(metrics),
        'timestamp': datetime.datetime.now().isoformat()
    })


def _build_labels(metric, label_keys):
    """构建 Prometheus 标签字符串"""
    parts = []
    for key in label_keys:
        value = metric.get(key, 'unknown')
        value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        parts.append(f'{key}="{value}"')
    return ','.join(parts)


def _scrape_loop():
    """定时检测循环（拉取模式）"""
    global SCRAPE_INTERVAL, TARGETS_LAST_SYNC
    SCRAPE_INTERVAL = AGENT_CONFIG.get('scrape_interval', DEFAULT_CONFIG['scrape_interval'])
    sync_interval = AGENT_CONFIG.get('sync_interval', DEFAULT_CONFIG['sync_interval'])
    
    # 启动时先尝试同步目标配置
    _sync_targets_from_server()
    
    # 立即执行一次检测
    scrape()
    
    while True:
        try:
            time.sleep(SCRAPE_INTERVAL)
            
            # 检测前从 Server 实时拉取最新目标（不再读本地文件）
            _sync_targets_from_server()
            
            # 执行检测
            scrape()
        except Exception as e:
            logger.error(f"检测异常: {e}")
            time.sleep(10)


def main():
    """主函数"""
    global AGENT_CONFIG, AGENT_MODE
    
    import argparse
    
    parser = argparse.ArgumentParser(description='SSL Certificate Agent')
    parser.add_argument('-c', '--config', help='配置文件路径')
    parser.add_argument('-i', '--interval', type=int, help='检测间隔（秒）')
    parser.add_argument('-t', '--timeout', type=int, help='连接超时（秒）')
    parser.add_argument('-p', '--port', type=int, help='监听端口')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址')
    parser.add_argument('-m', '--mode', choices=['push', 'pull', 'dual'], help='运行模式: push/pull/dual')
    
    args = parser.parse_args()
    
    # 加载配置
    AGENT_CONFIG = _load_config(args.config)
    
    # 命令行参数覆盖配置
    if args.interval:
        AGENT_CONFIG['scrape_interval'] = args.interval
        SCRAPE_INTERVAL = args.interval
    
    if args.timeout:
        AGENT_CONFIG['timeout'] = args.timeout
    
    if args.port:
        AGENT_CONFIG['listen_port'] = args.port
    
    if args.host:
        AGENT_CONFIG['listen_host'] = args.host
    
    # 解析运行模式
    if args.mode:
        AGENT_MODE = args.mode
    else:
        # 优先使用 AGENT_MODE 环境变量
        mode_env = os.getenv('AGENT_MODE', '').lower()
        if mode_env in ('push', 'pull', 'dual'):
            AGENT_MODE = mode_env
        else:
            # 兼容旧的 AGENT_PUSH_MODE
            _resolve_agent_mode()
            # 同时检查配置文件中的 agent_mode
            config_mode = AGENT_CONFIG.get('agent_mode', '')
            if config_mode in ('push', 'pull', 'dual'):
                AGENT_MODE = config_mode
    
    # push/dual 模式需要 server_url
    needs_server = AGENT_MODE in ('push', 'dual')
    
    logger.info("=" * 60)
    logger.info("SSL Certificate Agent 启动")
    logger.info(f"工作模式: {AGENT_MODE}")
    
    mode_desc = {
        'push': '推送模式（Agent 主动推送，不需要暴露端口）',
        'pull': '拉取模式（Server 主动拉取，需要暴露端口）',
        'dual': '双模式（同时暴露端口 + 主动推送，推荐）'
    }
    logger.info(f"模式说明: {mode_desc.get(AGENT_MODE, '')}")
    
    if AGENT_MODE in ('pull', 'dual'):
        listen_host = AGENT_CONFIG.get('listen_host', DEFAULT_CONFIG['listen_host'])
        listen_port = AGENT_CONFIG.get('listen_port', DEFAULT_CONFIG['listen_port'])
        logger.info(f"监听地址: {listen_host}:{listen_port}")
    
    if AGENT_CONFIG.get('enable_https'):
        logger.info("协议: HTTPS (启用)")
    else:
        logger.info("协议: HTTP")
    
    logger.info(f"检测间隔: {AGENT_CONFIG.get('scrape_interval')} 秒")
    logger.info(f"目标数量: {len(AGENT_CONFIG.get('targets', []))}")
    
    if needs_server and AGENT_CONFIG.get('server_url'):
        server_url = AGENT_CONFIG.get('server_url')
        if AGENT_CONFIG.get('enable_https') and server_url.startswith('http://'):
            server_url = server_url.replace('http://', 'https://')
        logger.info(f"Server URL: {server_url}")
        logger.info(f"Server SSL 验证: {AGENT_CONFIG.get('verify_ssl', True)}")
        logger.info(f"目标同步间隔: {AGENT_CONFIG.get('sync_interval')} 秒")
        logger.info(f"心跳间隔: {AGENT_CONFIG.get('heartbeat_interval', DEFAULT_CONFIG['heartbeat_interval'])} 秒")
        logger.info(f"推送间隔: {AGENT_CONFIG.get('push_interval', DEFAULT_CONFIG['push_interval'])} 秒")
    elif needs_server and not AGENT_CONFIG.get('server_url'):
        logger.warning("未配置 SERVER_URL，推送/双模式将无法工作！")
        logger.warning("请设置 SERVER_URL 环境变量指向 Server 地址")
    
    logger.info("=" * 60)
    
    if AGENT_MODE == 'push':
        logger.info("推送模式 API:")
        logger.info("  - POST → Server /api/v1/agents/<id>/metrics  推送指标")
        logger.info("  - POST → Server /api/v1/agents/<id>/heartbeat 发送心跳")
        logger.info("  - GET  ← Server /api/v1/agents/targets        拉取目标")
        logger.info("=" * 60)
        
        # 启动推送模式主循环（不启动 Flask 服务）
        push_thread = threading.Thread(target=_push_loop, daemon=True)
        push_thread.start()
        
        # 推送模式下不需要启动 HTTP 服务，但保持进程运行
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Agent 正在停止...")
    
    elif AGENT_MODE == 'pull':
        logger.info("拉取模式 API:")
        logger.info("  - GET /health          - 健康检查")
        logger.info("  - GET /info            - Agent 信息")
        logger.info("  - GET /metrics         - Prometheus 格式指标")
        logger.info("  - GET /api/v1/metrics  - JSON 格式指标")
        logger.info("  - GET /api/v1/targets  - 目标列表")
        logger.info("=" * 60)
        
        # 启动拉取模式定时检测线程
        scrape_thread = threading.Thread(target=_scrape_loop, daemon=True)
        scrape_thread.start()
        
        # 启动 HTTP/HTTPS 服务
        _start_http_server()
    
    elif AGENT_MODE == 'dual':
        logger.info("双模式 API:")
        logger.info("  [推送] POST → Server /api/v1/agents/<id>/metrics  推送指标")
        logger.info("  [推送] POST → Server /api/v1/agents/<id>/heartbeat 发送心跳")
        logger.info("  [推送] GET  ← Server /api/v1/agents/targets        拉取目标")
        logger.info("  [拉取] GET /health          - 健康检查")
        logger.info("  [拉取] GET /info            - Agent 信息")
        logger.info("  [拉取] GET /metrics         - Prometheus 格式指标")
        logger.info("  [拉取] GET /api/v1/metrics  - JSON 格式指标")
        logger.info("  [拉取] GET /api/v1/targets  - 目标列表")
        logger.info("=" * 60)
        
        # 启动推送模式主循环（在后台线程中运行）
        push_thread = threading.Thread(target=_push_loop, daemon=True)
        push_thread.start()
        
        # 启动 HTTP/HTTPS 服务（主线程）
        _start_http_server()


def _start_http_server():
    """启动 HTTP/HTTPS 服务（pull/dual 模式使用）"""
    listen_host = AGENT_CONFIG.get('listen_host', DEFAULT_CONFIG['listen_host'])
    listen_port = AGENT_CONFIG.get('listen_port', DEFAULT_CONFIG['listen_port'])
    enable_https = AGENT_CONFIG.get('enable_https', False)
    
    # 根据是否启用 HTTPS 配置 SSL
    ssl_context = None
    if enable_https:
        ssl_cert = os.getenv('SSL_CERT_FILE', '/app/certs/agent.crt')
        ssl_key = os.getenv('SSL_KEY_FILE', '/app/certs/agent.key')
        if os.path.exists(ssl_cert) and os.path.exists(ssl_key):
            ssl_context = (ssl_cert, ssl_key)
            logger.info(f"HTTPS 证书: {ssl_cert}")
            logger.info(f"HTTPS 私钥: {ssl_key}")
        else:
            logger.warning(f"HTTPS 证书文件不存在: {ssl_cert} 或 {ssl_key}")
            logger.warning("Agent 将以 HTTP 模式启动")
            enable_https = False
    
    # 启动 HTTP/HTTPS 服务
    app.run(
        host=listen_host,
        port=listen_port,
        debug=False,
        threaded=True,
        ssl_context=ssl_context
    )


if __name__ == '__main__':
    main()

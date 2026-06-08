#!/usr/bin/env python3
"""
SSL Certificate Monitoring - Server
服务端：支持两种模式
1. 直接监控模式：Server 直接检测可访问的目标
2. Agent 推送模式：Agent 主动推送指标数据和心跳到 Server（推荐）
3. Agent 拉取模式（兼容）：Server 从内网 Agent 拉取证书数据

功能：
1. 从配置文件读取目标列表和 Agent 列表
2. 直接检测可访问的目标（公网/内网可直连）
3. 接收 Agent 主动推送的指标数据（推送模式）
4. 从各 Agent 拉取内网目标的指标数据（拉取模式兼容）
5. 通过心跳判断 Agent 在线状态
6. 聚合所有指标数据，提供 Prometheus 格式查询接口
"""

from flask import Flask, request, jsonify
import json
import os
import time
import logging
import threading
import requests
import ssl
from urllib.parse import urlparse
import socket
import hashlib
import uuid
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, wait

# 导入数据库模块
import db

app = Flask(__name__)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 配置
CONFIG_PATH = os.getenv('SERVER_CONFIG_PATH', '/app/data/server_config.json')
DATA_PATH = os.getenv('SERVER_DATA_PATH', '/app/data/metrics.json')

# HTTPS 配置
ENABLE_HTTPS = os.getenv('ENABLE_HTTPS', 'false').lower() == 'true'
SSL_CERT_FILE = os.getenv('SSL_CERT_FILE', '/app/certs/server.crt')
SSL_KEY_FILE = os.getenv('SSL_KEY_FILE', '/app/certs/server.key')
VERIFY_SSL = os.getenv('SERVER_VERIFY_SSL', 'true').lower() == 'true'  # Server 端请求 Agent 时是否验证 SSL

# Agent 心跳超时
HEARTBEAT_TIMEOUT = int(os.getenv('HEARTBEAT_TIMEOUT', '300'))  # 心跳超时时间（秒）

# 指标缓存（内存中保留最新指标，用于 Prometheus /metrics 端点快速响应）
METRICS_BUFFER = []
METRICS_LOCK = threading.Lock()
MAX_METRICS_BUFFER = 10000

# 拉取配置
SCRAPE_INTERVAL = 60  # 拉取间隔（秒）
DIRECT_SCRAPE_INTERVAL = 60  # 直接监控间隔（秒）

# 目标/凭证文件路径（仅用于兼容性生成）
TARGETS_CONFIG_PATH = os.getenv('TARGETS_CONFIG_PATH', '/app/data/agent_targets.json')
UNIFIED_TARGETS_PATH = os.getenv('UNIFIED_TARGETS_PATH', '/app/data/ssl_targets.json')
AGENT_TARGETS_PATH = os.getenv('AGENT_TARGETS_PATH', '/app/agent_data/targets.json')
PROMETHEUS_TARGETS_PATH = os.getenv('PROMETHEUS_TARGETS_PATH', '/app/data/prometheus_targets.json')

# 凭证过期告警阈值
CREDENTIAL_WARN_DAYS = 30   # 小于30天预警
CREDENTIAL_CRIT_DAYS = 7    # 小于7天高危

# 凭证告警通知
CREDENTIAL_ALERT_WEBHOOK = os.getenv('CREDENTIAL_ALERT_WEBHOOK', '')  # 飞书 Webhook URL
CREDENTIAL_ALERT_EMAILS = os.getenv('CREDENTIAL_ALERT_EMAILS', '')    # 逗号分隔的告警邮箱
CREDENTIAL_ALERT_CHECK_INTERVAL = int(os.getenv('CREDENTIAL_ALERT_CHECK_INTERVAL', '3600'))  # 检查间隔（秒）
EXPORTER_RELOAD_URL = os.getenv('EXPORTER_RELOAD_URL', 'http://localhost:9116/reload')


def _parse_target_hostname_port(target_url):
    """从目标 URL 解析 hostname:port，用于清理关联的指标数据"""
    if not target_url:
        return None, None
    parsed = urlparse(target_url)
    hostname = parsed.hostname or target_url
    if parsed.port:
        port = str(parsed.port)
    elif parsed.scheme == 'https':
        port = '443'
    elif parsed.scheme == 'http':
        port = '80'
    else:
        port = '443'  # 默认 HTTPS
    return hostname, port


def _clean_metrics_for_target(target):
    """删除与目标关联的 METRICS_BUFFER 和数据库指标数据"""
    target_url = target.get('url', '')
    hostname, port = _parse_target_hostname_port(target_url)
    if not hostname:
        return
    
    # 构造 hostname 变体列表：同时匹配 www.xxx 和 xxx
    hostname_variants = {hostname}
    if hostname.startswith('www.'):
        hostname_variants.add(hostname[4:])  # www.google.com → google.com
    else:
        hostname_variants.add('www.' + hostname)  # google.com → www.google.com
    
    # 1. 从内存 METRICS_BUFFER 中清理（匹配所有 hostname 变体）
    with METRICS_LOCK:
        before = len(METRICS_BUFFER)
        METRICS_BUFFER[:] = [
            m for m in METRICS_BUFFER
            if not (m.get('hostname') in hostname_variants and str(m.get('port', '')) == port)
        ]
        removed = before - len(METRICS_BUFFER)
    
    # 2. 从数据库 metrics 表中清理（所有 hostname 变体）
    db_deleted = 0
    for hn in hostname_variants:
        db_deleted += db.delete_metrics_by_hostname_port(hn, port)
    
    if removed > 0 or db_deleted > 0:
        logger.info(f"清理目标 {target_url} 的指标数据: 内存 {removed} 条, 数据库 {db_deleted} 条")


def load_config():
    """加载配置（从数据库读取）"""
    agents = db.get_agents()
    settings_raw = db.get_all_settings()
    settings = {}
    for k, v in settings_raw.items():
        # 尝试解析 JSON 值
        try:
            settings[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            try:
                settings[k] = int(v)
            except (ValueError, TypeError):
                settings[k] = v
    return {"agents": agents, "settings": settings}


def save_config(config):
    """保存配置（写入数据库）"""
    agents = config.get('agents', [])
    for agent in agents:
        db.upsert_agent(agent)
    settings = config.get('settings', {})
    for k, v in settings.items():
        if isinstance(v, (dict, list)):
            db.set_setting(k, json.dumps(v, ensure_ascii=False))
        else:
            db.set_setting(k, str(v))


def load_targets_config():
    """加载目标配置（从数据库读取）"""
    targets = db.get_targets()
    logger.info(f"加载目标配置: {len(targets)} 个目标 (数据库)")
    return targets


def save_targets_config():
    """保存目标配置（数据已通过 upsert 写入数据库，此处仅同步辅助文件）"""
    try:
        # 生成 prometheus_targets.json 和 agent/data/targets.json
        db.generate_prometheus_targets(PROMETHEUS_TARGETS_PATH)
        db.generate_agent_targets(AGENT_TARGETS_PATH)
        logger.info("已同步辅助目标文件")
    except Exception as e:
        logger.error(f"同步辅助目标文件失败: {e}")


def trigger_exporter_reload():
    """触发 Exporter 重新加载配置"""
    try:
        resp = requests.get(EXPORTER_RELOAD_URL, timeout=10)
        if resp.status_code == 200:
            logger.info("Exporter 重新加载配置成功")
            return True
        else:
            logger.warning(f"Exporter 重新加载失败: HTTP {resp.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        logger.warning("无法连接到 Exporter")
        return False
    except Exception as e:
        logger.error(f"触发 Exporter 重载失败: {e}")
        return False


def load_metrics():
    """加载指标数据（从数据库读取最近30分钟）"""
    try:
        metrics = db.get_recent_metrics(minutes=30)
        return {"metrics": metrics, "last_updated": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"加载指标数据失败: {e}")
        return {"metrics": [], "last_updated": None}


def save_metrics(data):
    """保存指标数据（已弃用，保留接口兼容）"""
    pass


def _save_metrics_async(metrics):
    """异步保存指标数据到数据库"""
    try:
        db.insert_metrics(metrics)
    except Exception as e:
        logger.error(f"保存指标到数据库失败: {e}")


# ========== 凭证管理 ==========

def load_credentials_config():
    """加载凭证配置（从数据库读取）"""
    creds = db.get_credentials()
    logger.info(f"加载凭证配置: {len(creds)} 条记录 (数据库)")
    return creds


def save_credentials_config():
    """保存凭证配置（已弃用，数据已通过 upsert 写入数据库）"""
    pass


def compute_credential_status(expiry_date_str):
    """计算凭证过期状态"""
    if not expiry_date_str:
        return {'status': 'unknown', 'days_left': None, 'level': 'info'}
    try:
        expiry = datetime.strptime(expiry_date_str[:10], '%Y-%m-%d')
        now = datetime.now()
        days_left = (expiry - now).days
        if days_left < 0:
            return {'status': 'expired', 'days_left': days_left, 'level': 'critical'}
        elif days_left < CREDENTIAL_CRIT_DAYS:
            return {'status': 'critical', 'days_left': days_left, 'level': 'critical'}
        elif days_left < CREDENTIAL_WARN_DAYS:
            return {'status': 'warning', 'days_left': days_left, 'level': 'warning'}
        else:
            return {'status': 'normal', 'days_left': days_left, 'level': 'info'}
    except Exception:
        return {'status': 'unknown', 'days_left': None, 'level': 'info'}


def check_credential_expiry():
    """定时检查凭证过期并发送告警"""
    while True:
        try:
            time.sleep(CREDENTIAL_ALERT_CHECK_INTERVAL)
            credentials = db.get_credentials(enabled_only=True)
            if not credentials:
                continue
            
            alerts = []
            for cred in credentials:
                if not cred.get('expiry_date'):
                    continue
                info = compute_credential_status(cred['expiry_date'])
                if info['status'] in ('critical', 'expired'):
                    alerts.append({
                        'name': cred.get('name', ''),
                        'type': cred.get('type', ''),
                        'expiry_date': cred.get('expiry_date', ''),
                        'days_left': info['days_left'],
                        'level': 'critical',
                        'owner': cred.get('owner', ''),
                        'owner_email': cred.get('owner_email', ''),
                    })
                elif info['status'] == 'warning':
                    alerts.append({
                        'name': cred.get('name', ''),
                        'type': cred.get('type', ''),
                        'expiry_date': cred.get('expiry_date', ''),
                        'days_left': info['days_left'],
                        'level': 'warning',
                        'owner': cred.get('owner', ''),
                        'owner_email': cred.get('owner_email', ''),
                    })
            
            if not alerts:
                continue
            
            # 发送飞书告警
            if CREDENTIAL_ALERT_WEBHOOK:
                try:
                    send_feishu_credential_alert(alerts)
                except Exception as e:
                    logger.error(f"发送凭证过期飞书告警失败: {e}")
            
            # 发送邮件告警
            if CREDENTIAL_ALERT_EMAILS:
                try:
                    send_email_credential_alert(alerts)
                except Exception as e:
                    logger.error(f"发送凭证过期邮件告警失败: {e}")
                    
        except Exception as e:
            logger.error(f"凭证过期检查异常: {e}")


def send_feishu_credential_alert(alerts):
    """发送凭证过期飞书告警"""
    critical_alerts = [a for a in alerts if a['level'] == 'critical']
    warning_alerts = [a for a in alerts if a['level'] == 'warning']
    
    text_parts = ["🔐 凭证过期告警\n"]
    if critical_alerts:
        text_parts.append(f"🔴 高危（≤{CREDENTIAL_CRIT_DAYS}天/已过期）:")
        for a in critical_alerts:
            days_str = "已过期" if a['days_left'] < 0 else f"剩余{a['days_left']}天"
            text_parts.append(f"  • {a['name']}({a['type']}) - {a['expiry_date']} ({days_str})")
    if warning_alerts:
        text_parts.append(f"\n🟡 预警（≤{CREDENTIAL_WARN_DAYS}天）:")
        for a in warning_alerts:
            text_parts.append(f"  • {a['name']}({a['type']}) - {a['expiry_date']} (剩余{a['days_left']}天)")
    
    payload = {
        "msg_type": "text",
        "content": {"text": "\n".join(text_parts)}
    }
    requests.post(CREDENTIAL_ALERT_WEBHOOK, json=payload, timeout=10)
    logger.info(f"已发送凭证过期飞书告警: {len(alerts)} 条")


def send_email_credential_alert(alerts):
    """发送凭证过期邮件告警"""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    import smtplib
    
    smtp_host = os.getenv('SMTP_HOST', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_password = os.getenv('SMTP_PASSWORD', '')
    smtp_from = os.getenv('SMTP_FROM', smtp_user)
    smtp_use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
    
    if not smtp_host or not smtp_user:
        logger.warning("SMTP 未配置，跳过邮件告警")
        return
    
    emails = [e.strip() for e in CREDENTIAL_ALERT_EMAILS.split(',') if e.strip()]
    
    critical_alerts = [a for a in alerts if a['level'] == 'critical']
    warning_alerts = [a for a in alerts if a['level'] == 'warning']
    
    html_parts = [
        '<html><body>',
        '<h2>🔐 凭证过期告警</h2>',
        '<p>以下凭证即将过期或已过期，请及时处理：</p>',
    ]
    
    if critical_alerts:
        html_parts.append(f'<h3 style="color:red">🔴 高危告警（≤{CREDENTIAL_CRIT_DAYS}天或已过期）</h3>')
        html_parts.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">')
        html_parts.append('<tr style="background:#fee"><th>名称</th><th>类型</th><th>过期日期</th><th>剩余天数</th><th>负责人</th></tr>')
        for a in critical_alerts:
            days_str = "已过期" if a['days_left'] < 0 else f"{a['days_left']}天"
            html_parts.append(f'<tr><td>{a["name"]}</td><td>{a["type"]}</td><td>{a["expiry_date"]}</td><td style="color:red;font-weight:bold">{days_str}</td><td>{a["owner"]}</td></tr>')
        html_parts.append('</table>')
    
    if warning_alerts:
        html_parts.append(f'<h3 style="color:orange">🟡 预警（≤{CREDENTIAL_WARN_DAYS}天）</h3>')
        html_parts.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">')
        html_parts.append('<tr style="background:#ffe"><th>名称</th><th>类型</th><th>过期日期</th><th>剩余天数</th><th>负责人</th></tr>')
        for a in warning_alerts:
            html_parts.append(f'<tr><td>{a["name"]}</td><td>{a["type"]}</td><td>{a["expiry_date"]}</td><td style="color:orange;font-weight:bold">{a["days_left"]}天</td><td>{a["owner"]}</td></tr>')
        html_parts.append('</table>')
    
    html_parts.append('</body></html>')
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'[凭证过期告警] {len(critical_alerts)}个高危 / {len(warning_alerts)}个预警'
    msg['From'] = smtp_from
    msg['To'] = ', '.join(emails)
    msg.attach(MIMEText(''.join(html_parts), 'html', 'utf-8'))
    
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, emails, msg.as_string())
    
    logger.info(f"已发送凭证过期邮件告警到: {emails}")


def scrape_agent(agent):
    """从单个 Agent 拉取指标"""
    agent_id = agent.get('agent_id', '')
    host = agent.get('host', '')
    port = agent.get('port', 8091)
    name = agent.get('name', f"{host}:{port}")
    
    # 根据 Agent 配置决定协议，默认先尝试 HTTP
    use_https = agent.get('use_https', False)
    protocols = ["https", "http"] if use_https else ["http", "https"]
    
    for protocol in protocols:
        url = f"{protocol}://{host}:{port}/api/v1/metrics"
        try:
            logger.info(f"从 Agent [{name}] 拉取数据 (使用 {protocol.upper()})...")
            resp = requests.get(url, timeout=30, verify=VERIFY_SSL)
            resp.raise_for_status()
            
            data = resp.json()
            metrics = data.get('metrics', [])
            
            # 为每个指标添加 agent 信息
            enriched_metrics = []
            for m in metrics:
                m['agent_id'] = agent_id
                m['agent_name'] = name
                m['agent_hostname'] = data.get('agent_info', {}).get('hostname', host)
                m['scraped_at'] = datetime.now().isoformat()
                m['source'] = 'agent'  # 标记数据来源为 Agent
                enriched_metrics.append(m)
            
            logger.info(f"从 Agent [{name}] 拉取到 {len(metrics)} 条指标")
            return enriched_metrics, True
            
        except requests.exceptions.ConnectionError:
            logger.warning(f"无法连接 Agent [{name}] 通过 {protocol.upper()}")
            continue
        except requests.exceptions.Timeout:
            logger.warning(f"Agent [{name}] 连接超时 ({protocol.upper()})")
            continue
        except Exception as e:
            logger.error(f"从 Agent [{name}] 拉取失败 ({protocol.upper()}): {e}")
            continue
    
    return [], False


def _parse_target_url(url):
    """解析目标 URL"""
    result = {
        'host': None,
        'port': 443,
        'is_https': True
    }
    
    if '://' not in url:
        url = 'https://' + url
    
    parsed = requests.packages.urllib3.util.urlparse(url) if hasattr(requests.packages.urllib3.util, 'urlparse') else __import__('urllib.parse', fromlist=['urlparse']).urlparse(url)
    host_port = parsed.netloc or parsed.path
    
    if ':' in host_port:
        result['host'], port_str = host_port.rsplit(':', 1)
        result['port'] = int(port_str)
    else:
        result['host'] = host_port
        result['port'] = 443 if parsed.scheme == 'https' else 80
    
    result['is_https'] = parsed.scheme == 'https'
    
    return result


def _check_cert_directly(target):
    """直接检测单个目标的 SSL 证书"""
    url = target.get('url', '')
    timeout = target.get('timeout', 10)
    
    parsed = _parse_target_url(url)
    host = parsed['host']
    port = parsed['port']
    
    metrics = []
    
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        logger.info(f"[Direct] 检测证书: {host}:{port}")
        
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                
                if not cert_der:
                    metrics.append(_create_direct_metric('ssl_cert_check_success', 0, target))
                    return metrics
                
                try:
                    from cryptography import x509
                    from cryptography.hazmat.backends import default_backend
                    
                    cert_obj = x509.load_der_x509_certificate(cert_der, default_backend())
                    
                    not_after = cert_obj.not_valid_after_utc
                    not_before = cert_obj.not_valid_before_utc
                    now = datetime.now()
                    
                    time_diff = (not_after.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds()
                    days_left = round(time_diff / 86400, 1)
                    
                    # 提取证书详细信息
                    subject_info = {}
                    for attr in cert_obj.subject:
                        subject_info[attr.oid._name] = attr.value
                    
                    issuer_info = {}
                    for attr in cert_obj.issuer:
                        issuer_info[attr.oid._name] = attr.value
                    
                    subject_cn = subject_info.get('commonName', '')
                    issuer_cn = issuer_info.get('commonName', '')
                    issuer_org = issuer_info.get('organizationName', issuer_cn)
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
                    
                    # 带详细信息的指标
                    detail_labels = {
                        **base_labels,
                        'subject_cn': subject_cn,
                        'issuer_cn': issuer_cn,
                        'issuer_org': issuer_org,
                        'subject': json.dumps(subject_info),
                        'issuer': json.dumps(issuer_info)
                    }
                    
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
                    
                    logger.info(f"[Direct] 证书检测成功: {host}:{port}, 剩余 {days_left} 天, 颁发者: {issuer_org}")
                    
                except ImportError:
                    cert = ssock.getpeercert()
                    if cert and 'notAfter' in cert:
                        not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
                        now = datetime.now()
                        days_left = (not_after.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds() / 86400
                        
                        metrics.append(_create_direct_metric('ssl_cert_check_success', 1, target))
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
        logger.warning(f"[Direct] 连接超时: {host}:{port}")
        metrics.append(_create_direct_metric('ssl_cert_check_success', 0, target))
    except ssl.SSLError as e:
        logger.warning(f"[Direct] SSL 错误: {host}:{port} - {e}")
        metrics.append(_create_direct_metric('ssl_cert_check_success', 0, target))
    except Exception as e:
        logger.error(f"[Direct] 检测失败: {host}:{port} - {e}")
        metrics.append(_create_direct_metric('ssl_cert_check_success', 0, target))
    
    return metrics


def _check_cert_via_agent(agent, target):
    """通过 Agent 代理检测单个目标的 SSL 证书
    调用 Agent 的 POST /api/v1/check 端点，让 Agent 从它所在的网络环境去检测目标
    适用于 pull 模式 Agent：Agent 虽然 heartbeat 超时但 HTTP 可达的情况
    """
    host = agent.get('host', '')
    port = agent.get('port', 8091)
    name = agent.get('name', f"{host}:{port}")
    use_https = agent.get('use_https', False)
    protocols = ["https", "http"] if use_https else ["http", "https"]
    
    url = target.get('url', '')
    parsed = _parse_target_url(url)
    
    payload = {
        'url': url,
        'timeout': target.get('timeout', 30),
        'service_name': target.get('service_name', url),
        'owner': target.get('owner', 'unknown'),
        'owner_email': target.get('owner_email', ''),
        'env': target.get('env', 'production')
    }
    
    for protocol in protocols:
        check_url = f"{protocol}://{host}:{port}/api/v1/check"
        try:
            logger.info(f"[Agent代理] 请求 Agent [{name}] 检测: {parsed['host']}:{parsed['port']} (via {protocol.upper()})")
            resp = requests.post(check_url, json=payload, timeout=30, verify=VERIFY_SSL)
            resp.raise_for_status()
            
            data = resp.json()
            metrics = data.get('metrics', [])
            
            # 为每个指标添加 agent 来源信息
            for m in metrics:
                m['agent_id'] = agent.get('agent_id', '')
                m['agent_name'] = name
                m['agent_hostname'] = data.get('agent_info', {}).get('hostname', host)
                m['scraped_at'] = datetime.now().isoformat()
                m['source'] = 'agent_proxy'  # 标记为 agent 代理检测
            
            logger.info(f"[Agent代理] Agent [{name}] 返回 {len(metrics)} 条指标 for {parsed['host']}:{parsed['port']}")
            return metrics
            
        except requests.exceptions.ConnectionError:
            logger.warning(f"[Agent代理] 无法连接 Agent [{name}] via {protocol.upper()}")
            continue
        except requests.exceptions.Timeout:
            logger.warning(f"[Agent代理] Agent [{name}] 超时 ({protocol.upper()})")
            continue
        except Exception as e:
            logger.error(f"[Agent代理] Agent [{name}] 请求失败 ({protocol.upper()}): {e}")
            continue
    
    # Agent 完全不可达，返回失败指标
    logger.warning(f"[Agent代理] Agent [{name}] 完全不可达，回退到失败标记 for {parsed['host']}:{parsed['port']}")
    fail_metric = _create_direct_metric('ssl_cert_check_success', 0, target)
    fail_metric['source'] = 'agent_proxy_unreachable'
    return [fail_metric]


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


def _create_direct_metric(metric_name, value, target):
    """创建直接检测的指标对象"""
    parsed = _parse_target_url(target.get('url', ''))
    metric = {
        'metric_name': metric_name,
        'metric_type': metric_name,
        'value': value,
        'hostname': parsed['host'],
        'port': str(parsed['port']),
        'service_name': target.get('service_name', target.get('url', '')),
        'owner': target.get('owner', 'unknown'),
        'owner_email': target.get('owner_email', ''),
        'env': target.get('env', 'production'),
        'agent_hostname': target.get('_fallback_agent_host', 'unknown'),
    }
    return metric


def scrape_direct_targets():
    """检测未分配 Agent 的目标 + Agent 兜底目标
    - 未分配 Agent 的目标: Server 直接检测
    - 有 Agent 分配但 heartbeat 超时的目标: 全部通过 Agent API 代理检测（不走 Direct fallback）
    """
    # 1. 未分配 Agent 的目标（走 Direct）
    unassigned_targets = db.get_targets(agent_id='', enabled_only=True)
    
    # 2. 获取需要代理检测的 Agent 目标
    proxy_agent_targets = _get_agent_targets_for_proxy()
    
    if proxy_agent_targets:
        logger.info(f"发现 {len(proxy_agent_targets)} 个 Agent 目标，将通过 Agent 代理检测")
    
    if not unassigned_targets and not proxy_agent_targets:
        logger.debug("没有需要检测的目标")
        return []
    
    all_metrics = []
    
    # === 3. 通过 Agent API 代理检测所有 Agent 目标 ===
    for t in proxy_agent_targets:
        proxy_agent = t.pop('_proxy_agent', None)
        if proxy_agent:
            try:
                metrics = _check_cert_via_agent(proxy_agent, t)
                all_metrics.extend(metrics)
            except Exception as e:
                logger.error(f"Agent代理检测异常: {t.get('url', '')} - {e}")
                all_metrics.append(_create_direct_metric('ssl_cert_check_success', 0, t))
    
    # === 4. Server 直接检测未分配 Agent 的目标 ===
    if unassigned_targets:
        logger.info(f"开始直接检测 {len(unassigned_targets)} 个未分配 Agent 的目标...")
        
        max_workers = min(len(unassigned_targets), 10)
        
        def check_target(target):
            try:
                return _check_cert_directly(target)
            except Exception as e:
                logger.error(f"检测目标异常: {target.get('url', '')} - {e}")
                return [_create_direct_metric('ssl_cert_check_success', 0, target)]
        
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(check_target, t): t for t in unassigned_targets}
        
        done, not_done = wait(futures.keys(), timeout=60)
        
        for future in done:
            try:
                result = future.result()
                if result:
                    all_metrics.extend(result)
            except Exception as e:
                logger.error(f"获取结果异常: {e}")
        
        for future in not_done:
            future.cancel()
            target = futures[future]
            logger.warning(f"目标检测超时: {target.get('url', '')}")
        
        executor.shutdown(wait=False)
    
    # === 5. 添加时间戳 ===
    timestamp = datetime.now().isoformat()
    for m in all_metrics:
        m['timestamp'] = timestamp
    
    logger.info(f"目标检测完成，共 {len(all_metrics)} 条指标")
    return all_metrics


def _get_agent_targets_for_proxy():
    """获取所有 heartbeat 超时的 Agent 及其目标，全部走 Agent API 代理检测
    不再走 Direct fallback — Server 不自己检测，而是调用 Agent 的 /api/v1/check
    注意: heartbeat 正常的 Agent 目标由 scrape_agent() 处理，不在此处
    返回: 代理检测目标列表（每项附带了 _proxy_agent 字段）
    """
    agents = db.get_agents()
    agents_map = {a.get('agent_id'): a for a in agents if a.get('agent_id')}
    
    timeout_agent_ids = set()
    
    for agent in agents:
        agent_id = agent.get('agent_id', '')
        if not agent_id:
            continue
        
        # 检查 heartbeat
        heartbeat_info = db.get_heartbeat(agent_id)
        has_recent_hb = False
        if heartbeat_info:
            last_hb = heartbeat_info.get('last_heartbeat')
            if last_hb and isinstance(last_hb, str):
                last_hb = datetime.fromisoformat(last_hb)
            time_since_hb = (datetime.now() - last_hb).total_seconds() if last_hb else None
            if time_since_hb is not None and time_since_hb <= HEARTBEAT_TIMEOUT:
                has_recent_hb = True
        
        if has_recent_hb:
            # Agent 活跃，scrape_agent() 会处理
            continue
        
        # heartbeat 超时 → 全部走代理检测
        timeout_agent_ids.add(agent_id)
    
    if not timeout_agent_ids:
        return []
    
    # 获取这些 Agent 的所有目标
    all_targets = db.get_targets(enabled_only=True)
    proxy_targets = []
    
    for t in all_targets:
        target_agent_id = t.get('agent_id', '')
        if target_agent_id in timeout_agent_ids:
            agent = agents_map.get(target_agent_id, {})
            t['_fallback_agent_name'] = agent.get('name', t.get('agent_id', ''))
            t['_fallback_agent_host'] = agent.get('host', 'unknown')
            t['_proxy_agent'] = agent  # 携带 agent 配置供代理检测使用
            proxy_targets.append(t)
    
    return proxy_targets


def scrape_all_agents():
    """从所有 Agent 拉取数据 + 直接检测未分配 Agent 的目标
    - push 模式 Agent: 不拉取，它们会主动推送
    - pull 模式 Agent: 主动拉取
    - dual 模式 Agent: 主动拉取（它们也暴露了端口，拉取作为数据冗余）
    """
    global METRICS_BUFFER
    
    config = load_config()
    agents = config.get('agents', [])
    
    all_metrics = []
    
    # 1. 分类 Agent
    push_only_agents = []
    pull_agents = []
    dual_agents = []
    
    for a in agents:
        mode = a.get('agent_mode', '')
        if mode == 'push':
            push_only_agents.append(a)
        elif mode == 'dual':
            dual_agents.append(a)
        else:
            # 兼容旧的 push_mode 配置
            if a.get('push_mode', False) and mode != 'dual':
                push_only_agents.append(a)
            else:
                pull_agents.append(a)
    
    if push_only_agents:
        logger.debug(f"跳过 {len(push_only_agents)} 个推送模式 Agent（它们会主动推送数据）")
    
    # 从 pull 和 dual 模式的 Agent 拉取数据
    agents_to_pull = pull_agents + dual_agents
    
    if agents_to_pull:
        for agent in agents_to_pull:
            metrics, success = scrape_agent(agent)
            mode = agent.get('agent_mode', 'pull')
            for m in metrics:
                m['source'] = f'agent_{mode}'
            all_metrics.extend(metrics)
    else:
        if not push_only_agents and not dual_agents:
            logger.info("没有配置 Agent")
    
    # 2. 直接检测未分配 Agent 的目标
    direct_metrics = scrape_direct_targets()
    all_metrics.extend(direct_metrics)
    
    if all_metrics:
        with METRICS_LOCK:
            METRICS_BUFFER.extend(all_metrics)
            # 限制缓存大小
            if len(METRICS_BUFFER) > MAX_METRICS_BUFFER:
                METRICS_BUFFER[:] = METRICS_BUFFER[-MAX_METRICS_BUFFER:]
        
        # 异步保存到文件
        threading.Thread(target=_save_metrics_async, args=(all_metrics,), daemon=True).start()
        
        logger.info(f"本次共获取 {len(all_metrics)} 条指标 (Agent拉取: {len(all_metrics) - len(direct_metrics)}, 直接检测: {len(direct_metrics)})")


def _save_metrics_async(metrics):
    """异步保存指标数据"""
    data = load_metrics()
    data['metrics'].extend(metrics)
    data['last_updated'] = datetime.now().isoformat()
    # 保留最近 10000 条
    if len(data['metrics']) > 10000:
        data['metrics'] = data['metrics'][-10000:]
    save_metrics(data)


def _scrape_loop():
    """定时拉取循环"""
    global SCRAPE_INTERVAL, DIRECT_SCRAPE_INTERVAL
    
    config = load_config()
    SCRAPE_INTERVAL = config.get('settings', {}).get('scrape_interval', 60)
    DIRECT_SCRAPE_INTERVAL = config.get('settings', {}).get('direct_scrape_interval', 60)
    
    # 加载目标配置
    load_targets_config()
    
    logger.info(f"Server 启动，拉取间隔: {SCRAPE_INTERVAL} 秒，直接检测间隔: {DIRECT_SCRAPE_INTERVAL} 秒")
    logger.info("工作模式: Agent 拉取 + 直接检测（未分配 Agent 的目标）")
    
    # 立即执行一次
    scrape_all_agents()
    
    last_direct_scrape = time.time()
    
    while True:
        try:
            now = time.time()
            time.sleep(min(SCRAPE_INTERVAL, 10))  # 最多休眠10秒
            
            # 检查是否需要重新加载配置
            current_targets = load_targets_config()
            
            # 执行 Agent 拉取
            scrape_all_agents()
            
            # 如果距离上次直接检测的时间到了，执行直接检测
            if now - last_direct_scrape >= DIRECT_SCRAPE_INTERVAL:
                last_direct_scrape = now
                direct_metrics = scrape_direct_targets()
                if direct_metrics:
                    with METRICS_LOCK:
                        METRICS_BUFFER.extend(direct_metrics)
                        if len(METRICS_BUFFER) > MAX_METRICS_BUFFER:
                            METRICS_BUFFER[:] = METRICS_BUFFER[-MAX_METRICS_BUFFER:]
                    threading.Thread(target=_save_metrics_async, args=(direct_metrics,), daemon=True).start()
                    
        except Exception as e:
            logger.error(f"拉取异常: {e}")
            time.sleep(10)


# ========== API 接口 ==========

@app.route('/health')
def health():
    """健康检查"""
    config = load_config()
    agents = config.get('agents', [])
    
    return jsonify({
        'status': 'healthy',
        'service': 'ssl-cert-server',
        'agents_configured': len(agents),
        'metrics_buffer_size': len(METRICS_BUFFER)
    })


@app.route('/stats')
def stats():
    """统计信息"""
    config = load_config()
    agents = config.get('agents', [])
    
    # 判断 Agent 在线状态（优先心跳，其次探测）
    online_count = 0
    for agent in agents:
        probed = _probe_agent(agent)
        if probed['status'] == 'online':
            online_count += 1
    
    return jsonify({
        'status': 'success',
        'agents': {
            'total': len(agents),
            'online': online_count
        },
        'metrics': {
            'buffer_size': len(METRICS_BUFFER)
        },
        'scrape_interval': SCRAPE_INTERVAL,
        'heartbeat_timeout': HEARTBEAT_TIMEOUT
    })


def _probe_agent(agent):
    """探测 Agent 的在线状态和真实信息
    优先使用心跳记录判断在线状态（push/dual 模式），其次尝试主动探测（pull/dual 模式兼容）
    """
    agent_id = agent.get('agent_id', '')
    host = agent.get('host', '')
    port = agent.get('port', 8091)
    name = agent.get('name', f"{host}:{port}")
    
    result = {
        'agent_id': agent_id,
        'host': host,
        'port': port,
        'name': name,
        'enabled': agent.get('enabled', True),
        'hostname': None,
        'ip': None,
        'status': 'offline',
        'last_heartbeat': None,
        'metrics_count': 0,
        'push_mode': agent.get('push_mode', False),
        'agent_mode': agent.get('agent_mode', 'push' if agent.get('push_mode', False) else 'pull'),
        'error': None
    }
    
    # 1. 优先检查心跳记录（push/dual 模式）
    heartbeat_info = db.get_heartbeat(agent_id)
    
    if heartbeat_info:
        last_hb = heartbeat_info.get('last_heartbeat')
        if last_hb and isinstance(last_hb, str):
            last_hb = datetime.fromisoformat(last_hb)
        time_since_hb = (datetime.now() - last_hb).total_seconds() if last_hb else None
        hb_mode = heartbeat_info.get('agent_mode', 'push')
        
        if time_since_hb is not None and time_since_hb <= HEARTBEAT_TIMEOUT:
            # 心跳未超时，Agent 在线
            result['status'] = 'online'
            result['last_heartbeat'] = last_hb.isoformat() if hasattr(last_hb, 'isoformat') else str(last_hb)
            result['hostname'] = heartbeat_info.get('agent_info', {}).get('hostname') or host
            result['ip'] = heartbeat_info.get('agent_info', {}).get('ip') or host
            result['metrics_count'] = heartbeat_info.get('targets_count', 0)
            result['push_mode'] = hb_mode in ('push', 'dual')
            result['agent_mode'] = hb_mode
            result['local_targets_count'] = len(heartbeat_info.get('local_targets', []))
            return result
        elif time_since_hb is not None:
            # 心跳超时，但记录过
            result['last_heartbeat'] = last_hb.isoformat() if hasattr(last_hb, 'isoformat') else str(last_hb)
            result['hostname'] = heartbeat_info.get('agent_info', {}).get('hostname') or host
            result['ip'] = heartbeat_info.get('agent_info', {}).get('ip') or host
    
    # 2. 如果没有心跳记录或已超时，且 Agent 配置了端口，尝试主动探测（pull/dual 模式兼容）
    if port and port > 0:
        use_https = agent.get('use_https', False)
        protocols = ["https", "http"] if use_https else ["http", "https"]
        
        for protocol in protocols:
            urls_to_try = [
                f"{protocol}://{host}:{port}/info",
                f"{protocol}://{host}:{port}/health"
            ]
            
            for url in urls_to_try:
                try:
                    resp = requests.get(url, timeout=10, verify=VERIFY_SSL)
                    if resp.status_code == 200:
                        data = resp.json()
                        result['status'] = 'online'
                        result['last_heartbeat'] = datetime.now().isoformat()
                        
                        if url.endswith('/info'):
                            agent_info = data.get('agent_info', {})
                            result['hostname'] = agent_info.get('hostname') or host
                            result['ip'] = agent_info.get('ip') or host
                            result['metrics_count'] = data.get('config', {}).get('targets_count', 0)
                        else:
                            result['hostname'] = host
                            result['ip'] = host
                            result['metrics_count'] = data.get('metrics_buffer_size', 0)
                        
                        return result
                except requests.exceptions.ConnectionError:
                    result['error'] = 'connection_refused'
                    continue
                except requests.exceptions.Timeout:
                    result['error'] = 'timeout'
                    continue
                except Exception as e:
                    result['error'] = str(e)
                    continue
    
    # 所有尝试都失败
    return result


@app.route('/api/v1/agents', methods=['GET'])
def list_agents():
    """列出所有配置的 Agent（带实时状态探测）"""
    config = load_config()
    agents = config.get('agents', [])
    
    # 实时探测每个 Agent 的状态
    probed_agents = []
    for agent in agents:
        probed = _probe_agent(agent)
        probed_agents.append(probed)
    
    return jsonify({
        'status': 'success',
        'agents': probed_agents
    })


@app.route('/api/v1/agents', methods=['POST'])
def add_agent():
    """添加 Agent"""
    data = request.json or {}
    
    agent = {
        'agent_id': data.get('agent_id', str(time.time())),
        'host': data.get('host', ''),
        'port': data.get('port', 0),  # push 模式默认 port=0
        'name': data.get('name', f"{data.get('host', 'unknown')}"),
        'enabled': data.get('enabled', True),
        'push_mode': data.get('push_mode', True),  # 兼容旧字段
        'agent_mode': data.get('agent_mode', 'push'),  # 新字段: push/pull/dual
        'use_https': data.get('use_https', False)
    }
    
    # 根据 agent_mode 设置 push_mode 兼容字段
    if agent['agent_mode'] in ('push', 'dual'):
        agent['push_mode'] = True
    else:
        agent['push_mode'] = False
    
    # push 模式下 host 可以为空（Agent 主动连接 Server）
    # pull/dual 模式下 host 必填（Server 需要连接 Agent）
    if agent['agent_mode'] in ('pull', 'dual') and not agent['host']:
        return jsonify({'error': 'host is required for pull/dual mode agent'}), 400
    
    # 保存到配置
    config = load_config()
    if 'agents' not in config:
        config['agents'] = []
    
    # 检查是否已存在
    existing = False
    for i, a in enumerate(config['agents']):
        if a.get('agent_id') == agent['agent_id']:
            config['agents'][i] = agent
            existing = True
            break
        if agent['host'] and a.get('host') == agent['host'] and a.get('port') == agent['port']:
            config['agents'][i] = agent
            existing = True
            break
    
    if not existing:
        config['agents'].append(agent)
    
    save_config(config)
    
    # 探测一次，返回实时状态
    probed = _probe_agent(agent)
    
    logger.info(f"添加/更新 Agent: {agent['name']} (推送模式: {agent.get('push_mode', True)}), 状态: {probed['status']}")
    return jsonify({'status': 'success', 'agent': probed})


@app.route('/api/v1/agents/<agent_id>', methods=['DELETE'])
def delete_agent(agent_id):
    """删除 Agent"""
    db.delete_agent(agent_id)
    save_targets_config()  # 同步辅助文件
    
    logger.info(f"删除 Agent: {agent_id}")
    return jsonify({'status': 'success'})


@app.route('/api/v1/agents/<agent_id>/discover', methods=['POST'])
def discover_agent_targets(agent_id):
    """
    从 Agent 自动发现目标并同步到 Server
    将 Agent 本地的 targets.json 中的目标同步到数据库
    支持 pull/dual 模式（主动连接 Agent）和 push 模式（从心跳中获取）
    """
    config = load_config()
    agents = config.get('agents', [])
    
    # 找到对应的 Agent
    agent = None
    for a in agents:
        if a.get('agent_id') == agent_id:
            agent = a
            break
    
    if not agent:
        return jsonify({'status': 'error', 'message': 'Agent not found'}), 404
    
    agent_mode = agent.get('agent_mode', 'pull')
    host = agent.get('host')
    port = agent.get('port', 8091)
    
    # 对于 push 模式的 Agent，优先从心跳数据中获取本地目标
    if agent_mode == 'push':
        heartbeat_info = db.get_heartbeat(agent_id) or {}
        local_targets = heartbeat_info.get('local_targets', [])
        
        if local_targets:
            # 将心跳中的本地目标同步到数据库
            new_targets = []
            updated_count = 0
            added_count = 0
            
            for target_info in local_targets:
                target_url = target_info.get('url', '')
                if not target_url:
                    continue
                
                # 使用 uuid 避免与其他 Agent 的目标 ID 冲突
                existing = db.get_target_by_url(target_url, agent_id)
                
                new_target = {
                    'id': existing['id'] if existing else str(uuid.uuid4()),
                    'url': target_url,
                    'service_name': target_info.get('service_name', target_url),
                    'owner': target_info.get('owner', 'unknown'),
                    'owner_email': target_info.get('owner_email', ''),
                    'env': target_info.get('env', 'production'),
                    'agent_id': agent_id,
                    'enabled': target_info.get('enabled', True),
                    'timeout': target_info.get('timeout', 30),
                    'check_interval': target_info.get('check_interval', 180),
                    'synced_from_agent': True
                }
                
                if existing:
                    updated_count += 1
                else:
                    added_count += 1
                    new_targets.append(new_target)
                
                db.upsert_target(new_target)
            
            save_targets_config()
            
            threading.Thread(target=trigger_exporter_reload, daemon=True).start()
            
            logger.info(f"从推送模式 Agent {agent_id} 心跳同步目标: 新增 {added_count}, 更新 {updated_count}")
            
            return jsonify({
                'status': 'success',
                'message': f'从 Agent 心跳同步: 新增 {added_count}, 更新 {updated_count}',
                'added': added_count,
                'updated': updated_count,
                'targets': new_targets
            })
        
        # push 模式心跳无本地目标，尝试 pull 方式（如果有 host）
        if not host:
            return jsonify({
                'status': 'success',
                'message': '推送模式 Agent 暂无本地目标（Agent 会在心跳中上报目标列表）',
                'synced': 0,
                'total': 0,
                'targets': []
            })
        # 有 host 则 fallthrough 到 pull 模式
    
    # pull/dual 模式：主动连接 Agent 获取目标
    use_https = agent.get('use_https', False)
    protocols = ["https", "http"] if use_https else ["http", "https"]
    
    # 从 Agent 获取目标列表
    resp = None
    last_error = None
    for protocol in protocols:
        try:
            url = f"{protocol}://{host}:{port}/api/v1/targets"
            resp = requests.get(url, timeout=10, verify=VERIFY_SSL)
            if resp.status_code == 200:
                break
        except Exception as e:
            last_error = str(e)
            continue
    
    if not resp or resp.status_code != 200:
        return jsonify({
            'status': 'error', 
            'message': f'Agent returned HTTP {resp.status_code if resp else "connection_failed"}: {last_error or "unknown"}'
        }), 502
    
    data = resp.json()
    agent_targets = data.get('targets', [])
    
    if not agent_targets:
        return jsonify({
            'status': 'success',
            'message': 'No targets found on Agent',
            'synced': 0,
            'total': 0
        })
    
    # 同步目标到数据库
    try:
        new_targets = []
        updated_count = 0
        added_count = 0
        
        for target in agent_targets:
            target_url = target.get('url', '')
            if not target_url:
                continue
            
            # 构建目标数据 - 使用 uuid 避免与其他 Agent 的目标 ID 冲突
            existing = db.get_target_by_url(target_url, agent_id)
            
            new_target = {
                'id': existing['id'] if existing else str(uuid.uuid4()),
                'url': target_url,
                'service_name': target.get('service_name', target_url),
                'owner': target.get('owner', 'unknown'),
                'owner_email': target.get('owner_email', ''),
                'env': target.get('env', 'production'),
                'agent_id': agent_id,
                'enabled': target.get('enabled', True),
                'timeout': target.get('timeout', 10),
                'check_interval': target.get('check_interval', 180),
                'synced_from_agent': True
            }
            
            if existing:
                updated_count += 1
            else:
                added_count += 1
                new_targets.append(new_target)
            
            db.upsert_target(new_target)
        
        save_targets_config()
        
        # 触发 Exporter 重新加载
        threading.Thread(target=trigger_exporter_reload, daemon=True).start()
        
        logger.info(f"从 Agent {agent_id} 同步目标: 新增 {added_count}, 更新 {updated_count}")
        
        return jsonify({
            'status': 'success',
            'message': f'Synced {added_count} new targets, updated {updated_count} targets',
            'added': added_count,
            'updated': updated_count,
            'targets': new_targets
        })
        
    except requests.exceptions.ConnectionError:
        return jsonify({
            'status': 'error',
            'message': f'Cannot connect to Agent at {host}:{port}'
        }), 502
    except requests.exceptions.Timeout:
        return jsonify({
            'status': 'error',
            'message': f'Agent connection timeout'
        }), 504
    except Exception as e:
        logger.error(f"Discover agent targets error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/v1/agents/discover-all', methods=['POST'])
def discover_all_agents_targets():
    """
    从所有已配置的 Agent 自动发现并同步目标
    支持 push 模式（从心跳获取）和 pull/dual 模式（主动连接）
    """
    config = load_config()
    agents = config.get('agents', [])
    
    results = []
    total_added = 0
    total_updated = 0
    all_new_targets = []
    
    for agent in agents:
        if not agent.get('enabled', True):
            continue
            
        agent_id = agent.get('agent_id')
        agent_mode = agent.get('agent_mode', 'pull')
        host = agent.get('host')
        port = agent.get('port', 8091)
        agent_name = agent.get('name', f'{host}:{port}')
        
        added_count = 0
        updated_count = 0
        agent_new_targets = []
        
        # 1. push 模式：优先从心跳数据获取目标
        if agent_mode == 'push':
            heartbeat_info = db.get_heartbeat(agent_id) or {}
            local_targets = heartbeat_info.get('local_targets', [])
            
            if local_targets:
                for target_info in local_targets:
                    target_url = target_info.get('url', '')
                    if not target_url:
                        continue
                    
                    existing = db.get_target_by_url(target_url, agent_id)
                    new_target = {
                        'id': existing['id'] if existing else str(uuid.uuid4()),
                        'url': target_url,
                        'service_name': target_info.get('service_name', target_url),
                        'owner': target_info.get('owner', 'unknown'),
                        'owner_email': target_info.get('owner_email', ''),
                        'env': target_info.get('env', 'production'),
                        'agent_id': agent_id,
                        'enabled': target_info.get('enabled', True),
                        'timeout': target_info.get('timeout', 30),
                        'check_interval': target_info.get('check_interval', 180),
                        'synced_from_agent': True
                    }
                    
                    if existing:
                        updated_count += 1
                    else:
                        added_count += 1
                        agent_new_targets.append(new_target)
                    
                    db.upsert_target(new_target)
                
                if added_count > 0 or updated_count > 0:
                    save_targets_config()
                
                total_added += added_count
                total_updated += updated_count
                all_new_targets.extend(agent_new_targets)
                
                results.append({
                    'agent_id': agent_id,
                    'agent_name': agent_name,
                    'status': 'success',
                    'source': 'heartbeat',
                    'targets_found': len(local_targets),
                    'added': added_count,
                    'updated': updated_count
                })
                continue
            else:
                # 心跳无目标，尝试 pull 方式（如果有 host）
                if not host:
                    results.append({
                        'agent_id': agent_id,
                        'agent_name': agent_name,
                        'status': 'success',
                        'source': 'heartbeat',
                        'message': '推送模式 Agent 暂无本地目标',
                        'targets_found': 0,
                        'added': 0,
                        'updated': 0
                    })
                    continue
                # fallthrough 到 pull 模式
        
        # 2. pull/dual 模式：主动连接 Agent 获取目标
        use_https = agent.get('use_https', False)
        protocols = ["https", "http"] if use_https else ["http", "https"]
        
        resp = None
        last_error = None
        for protocol in protocols:
            try:
                url = f"{protocol}://{host}:{port}/api/v1/targets"
                resp = requests.get(url, timeout=10, verify=VERIFY_SSL)
                if resp.status_code == 200:
                    break
            except Exception as e:
                last_error = str(e)
                continue
        
        if not resp or resp.status_code != 200:
            results.append({
                'agent_id': agent_id,
                'agent_name': agent_name,
                'status': 'error',
                'source': 'pull',
                'message': f'HTTP {resp.status_code if resp else "connection_failed"}: {last_error or "unknown"}',
                'added': 0,
                'updated': 0
            })
            continue
        
        data = resp.json()
        agent_targets = data.get('targets', [])
        
        try:
            for target in agent_targets:
                target_url = target.get('url', '')
                if not target_url:
                    continue
                
                existing = db.get_target_by_url(target_url, agent_id)
                new_target = {
                    'id': existing['id'] if existing else str(uuid.uuid4()),
                    'url': target_url,
                    'service_name': target.get('service_name', target_url),
                    'owner': target.get('owner', 'unknown'),
                    'owner_email': target.get('owner_email', ''),
                    'env': target.get('env', 'production'),
                    'agent_id': agent_id,
                    'enabled': target.get('enabled', True),
                    'timeout': target.get('timeout', 10),
                    'check_interval': target.get('check_interval', 180),
                    'synced_from_agent': True
                }
                
                if existing:
                    updated_count += 1
                else:
                    added_count += 1
                    agent_new_targets.append(new_target)
                
                db.upsert_target(new_target)
            
            if added_count > 0 or updated_count > 0:
                save_targets_config()
            
            total_added += added_count
            total_updated += updated_count
            all_new_targets.extend(agent_new_targets)
            
            results.append({
                'agent_id': agent_id,
                'agent_name': agent_name,
                'status': 'success',
                'source': 'pull',
                'targets_found': len(agent_targets),
                'added': added_count,
                'updated': updated_count
            })
            
        except Exception as e:
            results.append({
                'agent_id': agent_id,
                'agent_name': agent.get('name', f'{host}:{port}'),
                'status': 'error',
                'message': str(e),
                'added': 0,
                'updated': 0
            })
    
    # 触发 Exporter 重新加载
    if total_added > 0 or total_updated > 0:
        threading.Thread(target=trigger_exporter_reload, daemon=True).start()
    
    logger.info(f"批量同步目标: 共 {total_added} 新增, {total_updated} 更新")
    
    return jsonify({
        'status': 'success',
        'message': f'Total: {total_added} added, {total_updated} updated',
        'total_added': total_added,
        'total_updated': total_updated,
        'found': total_added,
        'targets': all_new_targets,
        'results': results
    })


# ========== 目标管理 API ==========

@app.route('/api/v1/targets', methods=['GET'])
def list_targets():
    """列出非 Agent 管理的目标（普通目标，agent_id 为空）"""
    all_targets = db.get_targets()
    
    # ★ 只返回普通目标（agent_id 为空），Agent 目标在 /api/v1/agent-targets 中管理
    targets = [t for t in all_targets if not t.get('agent_id')]
    
    return jsonify({
        'status': 'success',
        'targets': targets
    })


@app.route('/api/v1/targets', methods=['POST'])
def add_target():
    """添加目标"""
    data = request.json or {}
    
    if not data.get('url'):
        return jsonify({'error': 'url is required'}), 400
    
    target = {
        'id': data.get('id', ''),
        'url': data.get('url'),
        'service_name': data.get('service_name', data.get('url')),
        'owner': data.get('owner', 'unknown'),
        'owner_email': data.get('owner_email', ''),
        'env': data.get('env', 'production'),
        'agent_id': data.get('agent_id', ''),
        'timeout': data.get('timeout', 10),
        'check_interval': data.get('check_interval', 180),
        'enabled': data.get('enabled', True),
        'created_at': datetime.now().isoformat()
    }
    
    db.upsert_target(target)
    save_targets_config()
    threading.Thread(target=trigger_exporter_reload, daemon=True).start()
    
    logger.info(f"添加目标: {target['url']}")
    return jsonify({'status': 'success', 'target': target})


@app.route('/api/v1/targets/<target_id>', methods=['PUT'])
def update_target(target_id):
    """更新目标"""
    data = request.json or {}
    
    existing = db.get_target(target_id)
    if not existing:
        return jsonify({'error': 'Target not found'}), 404
    
    updated = {
        **existing,
        'url': data.get('url', existing.get('url')),
        'service_name': data.get('service_name', existing.get('service_name')),
        'owner': data.get('owner', existing.get('owner')),
        'owner_email': data.get('owner_email', existing.get('owner_email')),
        'env': data.get('env', existing.get('env')),
        'agent_id': data.get('agent_id', existing.get('agent_id')),
        'timeout': data.get('timeout', existing.get('timeout', 10)),
        'check_interval': data.get('check_interval', existing.get('check_interval', 180)),
        'enabled': data.get('enabled', existing.get('enabled', True))
    }
    db.upsert_target(updated)
    save_targets_config()
    threading.Thread(target=trigger_exporter_reload, daemon=True).start()
    return jsonify({'status': 'success', 'target': updated})


@app.route('/api/v1/targets/<target_id>', methods=['DELETE'])
def delete_target(target_id):
    """删除目标"""
    if db.delete_target(target_id):
        save_targets_config()
        threading.Thread(target=trigger_exporter_reload, daemon=True).start()
        logger.info(f"删除目标: {target_id}")
        return jsonify({'status': 'success'})
    
    return jsonify({'error': 'Target not found'}), 404


@app.route('/api/v1/agents/targets', methods=['GET'])
def get_agent_targets():
    """
    Agent 获取分配给自己的目标配置
    用于 Agent 从 Server 同步目标
    支持多种匹配方式：
    1. 精确匹配 agent_id
    2. 根据 Agent host/IP 匹配
    3. 未分配的目标可被任何 Agent 获取
    """
    agent_id = request.args.get('agent_id', '')
    hostname = request.args.get('hostname', '')
    ip = request.args.get('ip', '')
    
    # 加载 Agent 配置用于匹配
    config = load_config()
    agents = config.get('agents', [])
    
    # 找到请求的 Agent 配置
    request_agent = None
    for a in agents:
        if a.get('agent_id') == agent_id or \
           (ip and a.get('host') == ip) or \
           (hostname and hostname in str(a.get('name', ''))):
            request_agent = a
            break
    
    targets = db.get_targets()
    
    # 筛选分配给该 Agent 或未分配的目标
    assigned_targets = []
    for t in targets:
        if not t.get('enabled', True):
            continue
        
        target_agent_id = t.get('agent_id', '')
        
        # 精确匹配 agent_id
        if target_agent_id == agent_id:
            assigned_targets.append(t)
            continue
        
        # 未分配的目标，可被任何 Agent 获取
        if not target_agent_id:
            assigned_targets.append(t)
            continue
        
        # 根据请求 Agent 的配置匹配（向后兼容旧格式）
        if request_agent:
            req_host = str(request_agent.get('host', ''))
            req_agent_id = str(request_agent.get('agent_id', ''))
            req_name = str(request_agent.get('name', ''))
            if target_agent_id == req_host or \
               target_agent_id == req_agent_id or \
               (target_agent_id and target_agent_id in req_name):
                assigned_targets.append(t)
    
    logger.info(f"Agent [{agent_id or hostname}] (IP: {ip}) 请求目标配置，返回 {len(assigned_targets)} 个")
    
    return jsonify({
        'status': 'success',
        'targets': assigned_targets,
        'agent_id': agent_id or hostname
    })


# ========== Agent 推送模式 API ==========

@app.route('/api/v1/agents/<agent_id>/metrics', methods=['POST'])
def receive_agent_metrics(agent_id):
    """
    接收 Agent 主动推送的指标数据
    推送模式下，Agent 检测完成后主动将指标推送到此端点
    """
    data = request.json or {}
    
    # 验证 agent_id 一致性
    payload_agent_id = data.get('agent_id', '')
    if payload_agent_id and payload_agent_id != agent_id:
        logger.warning(f"Agent ID 不匹配: URL={agent_id}, payload={payload_agent_id}")
    
    agent_info = data.get('agent_info', {})
    metrics = data.get('metrics', [])
    
    if not metrics:
        return jsonify({'status': 'success', 'message': 'No metrics to process', 'received': 0})
    
    # 为每个指标添加 agent 信息和来源标记
    enriched_metrics = []
    seen_keys = set()  # 用于去重
    
    for m in metrics:
        # 去重：按 hostname:port:metric_type:timestamp 判断
        dedup_key = f"{m.get('hostname', '')}:{m.get('port', '')}:{m.get('metric_type', '')}:{m.get('timestamp', '')}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        
        m['agent_id'] = agent_id
        m['agent_name'] = agent_info.get('hostname', agent_id)
        m['agent_hostname'] = agent_info.get('hostname', '')
        m['scraped_at'] = datetime.now().isoformat()
        m['source'] = 'agent_push'  # 标记数据来源为 Agent 推送
        enriched_metrics.append(m)
    
    # 添加到指标缓存
    with METRICS_LOCK:
        METRICS_BUFFER.extend(enriched_metrics)
        # 限制缓存大小
        if len(METRICS_BUFFER) > MAX_METRICS_BUFFER:
            METRICS_BUFFER[:] = METRICS_BUFFER[-MAX_METRICS_BUFFER:]
    
    # 异步保存到文件
    threading.Thread(target=_save_metrics_async, args=(enriched_metrics,), daemon=True).start()
    
    # 更新心跳（推送指标也视为心跳）
    agent_mode = data.get('agent_mode', 'push')
    db.update_heartbeat(agent_id, {
        'agent_info': agent_info,
        'targets_count': len(metrics),
        'push_mode': agent_mode in ('push', 'dual'),
        'agent_mode': agent_mode,
    })
    
    logger.info(f"接收 Agent [{agent_id}] 推送 {len(enriched_metrics)} 条指标 (去重后)")
    
    return jsonify({
        'status': 'success',
        'message': f'Received {len(enriched_metrics)} metrics',
        'received': len(enriched_metrics)
    })


@app.route('/api/v1/agents/<agent_id>/heartbeat', methods=['POST'])
def receive_agent_heartbeat(agent_id):
    """
    接收 Agent 心跳
    用于判断 Agent 在线状态
    """
    data = request.json or {}
    
    payload_agent_id = data.get('agent_id', '')
    if payload_agent_id and payload_agent_id != agent_id:
        logger.warning(f"心跳 Agent ID 不匹配: URL={agent_id}, payload={payload_agent_id}")
    
    agent_info = data.get('agent_info', {})
    targets_count = data.get('targets_count', 0)
    metrics_buffer_size = data.get('metrics_buffer_size', 0)
    push_queue_size = data.get('push_queue_size', 0)
    agent_config = data.get('config', {})
    local_targets = data.get('local_targets', [])
    
    # 更新心跳记录到数据库
    db.update_heartbeat(agent_id, {
        'agent_info': agent_info,
        'targets_count': targets_count,
        'metrics_buffer_size': metrics_buffer_size,
        'push_queue_size': push_queue_size,
        'push_mode': agent_config.get('push_mode', True),
        'agent_mode': agent_config.get('agent_mode', 'push' if agent_config.get('push_mode', True) else 'pull'),
        'scrape_interval': agent_config.get('scrape_interval', 180),
        'local_targets': local_targets,
    })
    
    # 如果 Agent 不在配置列表中，自动注册
    existing_agent = db.get_agent(agent_id)
    if not existing_agent and agent_info:
        # 自动注册 Agent
        agent_mode = agent_config.get('agent_mode', 'push')
        new_agent = {
            'agent_id': agent_id,
            'host': agent_info.get('ip', ''),
            'port': 0 if agent_mode == 'push' else 8091,  # push 模式不需要端口
            'name': agent_info.get('hostname', agent_id),
            'enabled': True,
            'push_mode': agent_mode in ('push', 'dual'),
            'agent_mode': agent_mode,
            'auto_registered': True
        }
        db.upsert_agent(new_agent)
        logger.info(f"自动注册推送模式 Agent: {agent_id} ({agent_info.get('hostname', '')})")
    
    logger.debug(f"接收 Agent [{agent_id}] 心跳, 目标: {targets_count}, 指标缓存: {metrics_buffer_size}")
    
    return jsonify({
        'status': 'success',
        'message': 'Heartbeat received'
    })


@app.route('/api/v1/scrape', methods=['POST'])
def manual_scrape():
    """手动触发一次拉取"""
    threading.Thread(target=scrape_all_agents, daemon=True).start()
    return jsonify({'status': 'success', 'message': 'Scrape triggered'})


# ========== Prometheus 数据接口 ==========

@app.route('/metrics')
def prometheus_metrics():
    """
    Prometheus 格式的指标数据
    支持 Prometheus 抓取
    """
    with METRICS_LOCK:
        metrics = list(METRICS_BUFFER)
    
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
    output_lines.append('# HELP ssl_agent_health Agent health status (1=online, 0=offline)')
    output_lines.append('# TYPE ssl_agent_health gauge')
    
    # 按指标类型分组
    metrics_by_type = {}
    for m in metrics:
        metric_type = m.get('metric_type', 'unknown')
        if metric_type not in metrics_by_type:
            metrics_by_type[metric_type] = []
        metrics_by_type[metric_type].append(m)
    
    # 基础标签
    base_label_keys = ['hostname', 'port', 'service_name', 'owner', 'owner_email', 'agent_hostname', 'env', 'source']
    
    # 带证书详细信息的标签
    detail_label_keys = ['hostname', 'port', 'service_name', 'owner', 'owner_email', 'agent_hostname', 'env', 'source',
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

    # ========== 凭证管理指标 ==========
    output_lines.append('# HELP credential_days_left Days left until credential expiry')
    output_lines.append('# TYPE credential_days_left gauge')

    cred_label_keys = ['credential_name', 'credential_type', 'owner', 'owner_email', 'service_name', 'env']

    credentials = db.get_credentials(enabled_only=True)
    for cred in credentials:
        if not cred.get('expiry_date'):
            continue
        info = compute_credential_status(cred['expiry_date'])
        cred_metric = {
            'credential_name': cred.get('name', ''),
            'credential_type': cred.get('type', ''),
            'owner': cred.get('owner', ''),
            'owner_email': cred.get('owner_email', ''),
            'service_name': cred.get('service_name', ''),
            'env': cred.get('env', ''),
        }
        labels = _build_labels(cred_metric, cred_label_keys)
        output_lines.append(f'credential_days_left{{{labels}}} {info["days_left"]}')

    return '\n'.join(output_lines) + '\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}


def _build_labels(metric, label_keys):
    """构建 Prometheus 标签字符串"""
    parts = []
    for key in label_keys:
        value = metric.get(key, 'unknown')
        value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        parts.append(f'{key}="{value}"')
    return ','.join(parts)


@app.route('/api/v1/query', methods=['GET'])
def query():
    """PromQL 查询接口（简化版）"""
    query_str = request.args.get('query', '')
    
    with METRICS_LOCK:
        metrics = list(METRICS_BUFFER)
    
    results = []
    for m in metrics:
        if query_str in m.get('metric_name', ''):
            results.append({
                'metric': {k: v for k, v in m.items() if k not in ['value', 'metric_name', 'metric_type', 'scraped_at', 'agent_id']},
                'value': [time.time(), m.get('value', 0)]
            })
    
    return jsonify({
        'status': 'success',
        'data': {
            'resultType': 'vector',
            'result': results
        }
    })


# ========== Web 管理界面 ==========

@app.route('/')
def index():
    """Server 状态页面"""
    config = load_config()
    agents = config.get('agents', [])
    scrape_interval = config.get('settings', {}).get('scrape_interval', 60)
    
    agents_html = ''
    for agent in agents:
        agents_html += f'''
        <tr>
            <td><strong>{agent.get('name', 'Unknown')}</strong></td>
            <td>{agent.get('host')}:{agent.get('port', 8091)}</td>
            <td><span class="status {'status-online' if agent.get('enabled', True) else 'status-offline'}">
                {'已启用' if agent.get('enabled', True) else '已禁用'}
            </span></td>
            <td>{agent.get('agent_id', '-')}</td>
        </tr>
        '''
    
    if not agents:
        agents_html = '<tr><td colspan="4" class="empty-state">暂无配置的 Agent</td></tr>'
    
    return f'''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>SSL 证书监控 - Server</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; min-height: 100vh; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .header h1 {{ font-size: 24px; margin-bottom: 5px; }}
            .header p {{ opacity: 0.9; font-size: 14px; }}
            .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
            .stats-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
            .stat-card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
            .stat-card .label {{ color: #666; font-size: 14px; margin-bottom: 8px; }}
            .stat-card .value {{ font-size: 32px; font-weight: bold; color: #333; }}
            .card {{ background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 24px; }}
            .card-header {{ margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #f0f0f0; display: flex; justify-content: space-between; align-items: center; }}
            .card-header h2 {{ font-size: 18px; color: #333; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 14px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }}
            th {{ background: #fafafa; font-weight: 600; color: #333; font-size: 13px; text-transform: uppercase; }}
            td {{ font-size: 14px; color: #666; }}
            tr:hover {{ background: #fafafa; }}
            .status {{ padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }}
            .status-online {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
            .status-offline {{ background: #fff2e8; color: #fa8c16; border: 1px solid #ffbb96; }}
            .empty-state {{ text-align: center; padding: 60px 20px; color: #999; }}
            .btn {{ padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 14px; }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-primary:hover {{ background: #5a6fd6; }}
            .info-box {{ margin-top: 20px; padding: 16px; background: #f6f8fa; border-radius: 8px; }}
            .info-box h4 {{ margin-bottom: 12px; color: #24292f; }}
            .info-box p {{ font-size: 13px; color: #57606a; margin-bottom: 8px; }}
            .info-box code {{ background: #e8e8e8; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>SSL Certificate Server</h1>
            <p>主动拉取 Agent 数据（不对外暴露端口）</p>
        </div>
        
        <div class="container">
            <div class="stats-row">
                <div class="stat-card">
                    <div class="label">配置的 Agent</div>
                    <div class="value" id="stat-agents">{len(agents)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">指标缓存</div>
                    <div class="value" id="stat-metrics">-</div>
                </div>
                <div class="stat-card">
                    <div class="label">拉取间隔</div>
                    <div class="value">{scrape_interval}秒</div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <h2>Agent 列表</h2>
                    <button class="btn btn-primary" onclick="addAgent()">+ 添加 Agent</button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>名称</th>
                            <th>地址</th>
                            <th>状态</th>
                            <th>Agent ID</th>
                        </tr>
                    </thead>
                    <tbody id="agents-body">
                        {agents_html}
                    </tbody>
                </table>
            </div>
            
            <div class="info-box">
                <h4>使用说明</h4>
                <p>1. Server 不对外暴露端口，仅通过内网拉取 Agent 数据</p>
                <p>2. Agent 需要暴露 <code>8091</code> 端口供 Server 拉取</p>
                <p>3. Server 定时从各 Agent 拉取指标数据</p>
                <p>4. Prometheus 可通过 <code>/metrics</code> 接口获取数据</p>
            </div>
        </div>
        
        <script>
            async function loadStats() {{
                try {{
                    const resp = await fetch('/stats');
                    const data = await resp.json();
                    document.getElementById('stat-metrics').textContent = data.metrics?.buffer_size || 0;
                }} catch (e) {{
                    console.error('Failed to load stats:', e);
                }}
            }}
            
            function addAgent() {{
                const host = prompt('请输入 Agent IP 地址:');
                if (!host) return;
                const port = prompt('请输入 Agent 端口 (默认 8091):') || '8091';
                const name = prompt('请输入 Agent 名称:') || host;
                
                fetch('/api/v1/agents', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ host, port: parseInt(port), name }})
                }})
                .then(r => r.json())
                .then(d => {{
                    if (d.status === 'success') {{
                        location.reload();
                    }}
                }});
            }}
            
            document.addEventListener('DOMContentLoaded', () => {{
                loadStats();
                setInterval(loadStats, 10000);
            }});
        </script>
    </body>
    </html>
    '''


# ========== Agent 目标管理 ==========

@app.route('/targets')
def targets_page():
    """Agent 目标管理页面"""
    config = load_config()
    agents = config.get('agents', [])
    
    return f'''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Agent 目标管理 - SSL 证书监控</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; min-height: 100vh; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; }}
            .header h1 {{ font-size: 24px; margin-bottom: 5px; }}
            .header p {{ opacity: 0.9; font-size: 14px; }}
            .header a {{ color: white; text-decoration: none; padding: 8px 16px; background: rgba(255,255,255,0.2); border-radius: 6px; }}
            .header a:hover {{ background: rgba(255,255,255,0.3); }}
            .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
            .card {{ background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 24px; }}
            .card-header {{ margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #f0f0f0; display: flex; justify-content: space-between; align-items: center; }}
            .card-header h2 {{ font-size: 18px; color: #333; }}
            .btn {{ padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 14px; margin-left: 8px; }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-primary:hover {{ background: #5a6fd6; }}
            .btn-danger {{ background: #ff4d4f; color: white; }}
            .btn-danger:hover {{ background: #ff7875; }}
            .btn-warning {{ background: #faad14; color: white; }}
            .btn-warning:hover {{ background: #ffc53d; }}
            .btn-success {{ background: #52c41a; color: white; }}
            .btn-success:hover {{ background: #73d13d; }}
            .btn-sm {{ padding: 4px 12px; font-size: 12px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 14px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }}
            th {{ background: #fafafa; font-weight: 600; color: #333; font-size: 13px; }}
            td {{ font-size: 14px; color: #666; }}
            tr:hover {{ background: #fafafa; }}
            .status {{ padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; display: inline-block; }}
            .status-enabled {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
            .status-disabled {{ background: #f5f5f5; color: #999; border: 1px solid #d9d9d9; }}
            .empty-state {{ text-align: center; padding: 60px 20px; color: #999; }}
            .agent-selector {{ margin-bottom: 20px; }}
            .agent-selector select {{ padding: 10px 16px; border-radius: 6px; border: 1px solid #d9d9d9; font-size: 14px; min-width: 200px; }}
            .modal {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; }}
            .modal.show {{ display: flex; align-items: center; justify-content: center; }}
            .modal-content {{ background: white; border-radius: 12px; padding: 24px; width: 500px; max-width: 90%; }}
            .modal-header {{ margin-bottom: 20px; }}
            .modal-header h3 {{ font-size: 18px; color: #333; }}
            .modal-body {{ margin-bottom: 20px; }}
            .form-group {{ margin-bottom: 16px; }}
            .form-group label {{ display: block; margin-bottom: 6px; font-size: 14px; color: #666; }}
            .form-group input, .form-group select {{ width: 100%; padding: 10px 12px; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 14px; }}
            .form-group input:focus, .form-group select:focus {{ outline: none; border-color: #667eea; }}
            .modal-footer {{ display: flex; justify-content: flex-end; gap: 8px; }}
            .loading {{ text-align: center; padding: 40px; color: #999; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h1>Agent 目标管理</h1>
                <p>管理 Agent 监控的 SSL 证书目标</p>
            </div>
            <a href="/">← 返回首页</a>
        </div>
        
        <div class="container">
            <div class="card">
                <div class="card-header">
                    <h2>Agent 目标列表</h2>
                    <div>
                        <button class="btn btn-warning" onclick="refreshTargets()">刷新</button>
                    </div>
                </div>
                
                <div class="agent-selector">
                    <select id="agent-filter" onchange="loadTargets()">
                        <option value="">所有 Agent</option>
                    </select>
                </div>
                
                <div id="targets-container">
                    <div class="loading">加载中...</div>
                </div>
            </div>
        </div>
        
        <!-- 添加/编辑目标 Modal -->
        <div id="target-modal" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3 id="modal-title">添加目标</h3>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="target-id">
                    <input type="hidden" id="target-agent-id">
                    <div class="form-group">
                        <label>Agent *</label>
                        <select id="target-agent-select" required>
                            <option value="">请选择 Agent</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>URL *</label>
                        <input type="text" id="target-url" placeholder="https://example.com:8443" required>
                    </div>
                    <div class="form-group">
                        <label>服务名称</label>
                        <input type="text" id="target-service-name" placeholder="服务名称">
                    </div>
                    <div class="form-group">
                        <label>负责人</label>
                        <input type="text" id="target-owner" placeholder="负责人姓名">
                    </div>
                    <div class="form-group">
                        <label>负责人邮箱</label>
                        <input type="email" id="target-owner-email" placeholder="email@example.com">
                    </div>
                    <div class="form-group">
                        <label>环境</label>
                        <select id="target-env">
                            <option value="production">生产环境</option>
                            <option value="staging">预发布环境</option>
                            <option value="test">测试环境</option>
                            <option value="development">开发环境</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>检测间隔（秒）</label>
                        <input type="number" id="target-check-interval" value="180" min="60">
                    </div>
                    <div class="form-group">
                        <label>超时（秒）</label>
                        <input type="number" id="target-timeout" value="30" min="5">
                    </div>
                    <div class="form-group">
                        <label>启用</label>
                        <select id="target-enabled">
                            <option value="true">是</option>
                            <option value="false">否</option>
                        </select>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn" onclick="closeModal()">取消</button>
                    <button class="btn btn-primary" onclick="saveTarget()">保存</button>
                </div>
            </div>
        </div>
        
        <script>
            let agents = [];
            
            async function loadAgents() {{
                try {{
                    const resp = await fetch('/api/v1/agents');
                    const data = await resp.json();
                    agents = data.agents || [];
                    
                    // 填充 Agent 选择器 - 使用 agent_id 作为值
                    const agentFilter = document.getElementById('agent-filter');
                    const agentSelect = document.getElementById('target-agent-select');
                    
                    agents.forEach(agent => {{
                        // 使用 agent_id 作为值，便于 API 匹配
                        const value = agent.agent_id || agent.host;
                        const label = agent.name || agent.hostname || agent.host || value;
                        const option = `<option value="${{value}}">${{label}}</option>`;
                        agentFilter.innerHTML += option;
                        agentSelect.innerHTML += option;
                    }});
                    
                    loadTargets();
                }} catch (e) {{
                    console.error('Failed to load agents:', e);
                }}
            }}
            
            async function loadTargets() {{
                const container = document.getElementById('targets-container');
                container.innerHTML = '<div class="loading">加载中...</div>';
                
                try {{
                    const agentFilter = document.getElementById('agent-filter').value;
                    const resp = await fetch('/api/v1/agent-targets' + (agentFilter ? '?agent_host=' + agentFilter : ''));
                    const data = await resp.json();
                    
                    if (!data.targets || data.targets.length === 0) {{
                        container.innerHTML = '<div class="empty-state">暂无目标配置</div>';
                        return;
                    }}
                    
                    let html = `<table>
                        <thead>
                            <tr>
                                <th>URL</th>
                                <th>服务名称</th>
                                <th>Agent</th>
                                <th>负责人</th>
                                <th>邮箱</th>
                                <th>环境</th>
                                <th>间隔</th>
                                <th>状态</th>
                                <th>操作</th>
                            </tr>
                        </thead>
                        <tbody>`;
                    
                    data.targets.forEach(t => {{
                        html += `<tr>
                            <td><code>${{t.url}}</code></td>
                            <td>${{t.service_name || '-'}}</td>
                            <td>${{t.agent_name || t.agent_host || '-'}}</td>
                            <td>${{t.owner || '-'}}</td>
                            <td>${{t.owner_email || '-'}}</td>
                            <td><span class="status ${{t.env === 'production' ? 'status-enabled' : 'status-disabled'}}">${{t.env || 'production'}}</span></td>
                            <td>${{t.check_interval || 180}}秒</td>
                            <td><span class="status ${{t.enabled ? 'status-enabled' : 'status-disabled'}}">${{t.enabled ? '已启用' : '已禁用'}}</span></td>
                            <td>-</td>
                        </tr>`;
                    }});
                    
                    html += '</tbody></table>';
                    container.innerHTML = html;
                    
                }} catch (e) {{
                    console.error('Failed to load targets:', e);
                    container.innerHTML = '<div class="empty-state">加载失败: ' + e.message + '</div>';
                }}
            }}
            
            function refreshTargets() {{
                loadTargets();
            }}
            
            function showAddModal() {{
                document.getElementById('modal-title').textContent = '添加目标';
                document.getElementById('target-id').value = '';
                document.getElementById('target-agent-select').value = document.getElementById('agent-filter').value;
                document.getElementById('target-url').value = '';
                document.getElementById('target-service-name').value = '';
                document.getElementById('target-owner').value = '';
                document.getElementById('target-owner-email').value = '';
                document.getElementById('target-env').value = 'production';
                document.getElementById('target-check-interval').value = '180';
                document.getElementById('target-timeout').value = '30';
                document.getElementById('target-enabled').value = 'true';
                document.getElementById('target-modal').classList.add('show');
            }}
            
            async function editTarget(targetId, agentId) {{
                try {{
                    const resp = await fetch('/api/v1/agent-targets/' + targetId + '?agent_id=' + agentId);
                    const data = await resp.json();
                    if (data.target) {{
                        const t = data.target;
                        document.getElementById('modal-title').textContent = '编辑目标';
                        document.getElementById('target-id').value = t.id;
                        document.getElementById('target-agent-id').value = agentId;
                        document.getElementById('target-agent-select').value = t.agent_id || '';
                        document.getElementById('target-url').value = t.url;
                        document.getElementById('target-service-name').value = t.service_name || '';
                        document.getElementById('target-owner').value = t.owner || '';
                        document.getElementById('target-owner-email').value = t.owner_email || '';
                        document.getElementById('target-env').value = t.env || 'production';
                        document.getElementById('target-check-interval').value = t.check_interval || 180;
                        document.getElementById('target-timeout').value = t.timeout || 30;
                        document.getElementById('target-enabled').value = String(t.enabled !== false);
                        document.getElementById('target-modal').classList.add('show');
                    }}
                }} catch (e) {{
                    alert('获取目标信息失败: ' + e.message);
                }}
            }}
            
            async function saveTarget() {{
                const id = document.getElementById('target-id').value;
                const oldAgentId = document.getElementById('target-agent-id').value;
                const agentId = document.getElementById('target-agent-select').value;
                const url = document.getElementById('target-url').value.trim();
                
                if (!agentId) {{ alert('请选择 Agent'); return; }}
                if (!url) {{ alert('请输入 URL'); return; }}
                
                const target = {{
                    url: url,
                    service_name: document.getElementById('target-service-name').value,
                    owner: document.getElementById('target-owner').value,
                    owner_email: document.getElementById('target-owner-email').value,
                    env: document.getElementById('target-env').value,
                    check_interval: parseInt(document.getElementById('target-check-interval').value) || 180,
                    timeout: parseInt(document.getElementById('target-timeout').value) || 30,
                    enabled: document.getElementById('target-enabled').value === 'true',
                    agent_id: agentId
                }};
                
                try {{
                    let resp;
                    if (id) {{
                        resp = await fetch('/api/v1/agent-targets/' + id, {{
                            method: 'PUT',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ ...target, old_agent_id: oldAgentId }})
                        }});
                    }} else {{
                        resp = await fetch('/api/v1/agent-targets', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify(target)
                        }});
                    }}
                    
                    const data = await resp.json();
                    if (data.status === 'success') {{
                        closeModal();
                        loadTargets();
                    }} else {{
                        alert('保存失败: ' + (data.message || '未知错误'));
                    }}
                }} catch (e) {{
                    alert('保存失败: ' + e.message);
                }}
            }}
            
            async function toggleTarget(targetId, agentId, enabled) {{
                try {{
                    const resp = await fetch('/api/v1/agent-targets/' + targetId, {{
                        method: 'PUT',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ enabled, agent_id: agentId }})
                    }});
                    const data = await resp.json();
                    if (data.status === 'success') {{
                        loadTargets();
                    }} else {{
                        alert('操作失败: ' + (data.message || '未知错误'));
                    }}
                }} catch (e) {{
                    alert('操作失败: ' + e.message);
                }}
            }}
            
            async function deleteTarget(targetId, agentId) {{
                if (!confirm('确定要删除这个目标吗？')) return;
                try {{
                    const resp = await fetch('/api/v1/agent-targets/' + targetId + '?agent_id=' + agentId, {{
                        method: 'DELETE'
                    }});
                    const data = await resp.json();
                    if (data.status === 'success') {{
                        loadTargets();
                    }} else {{
                        alert('删除失败: ' + (data.message || '未知错误'));
                    }}
                }} catch (e) {{
                    alert('删除失败: ' + e.message);
                }}
            }}
            
            function closeModal() {{
                document.getElementById('target-modal').classList.remove('show');
            }}
            
            // 点击模态框外部关闭
            document.getElementById('target-modal').addEventListener('click', function(e) {{
                if (e.target === this) closeModal();
            }});
            
            loadAgents();
        </script>
    </body>
    </html>
    '''


# ========== Agent 目标 API ==========

@app.route('/api/v1/agent-targets', methods=['GET'])
def list_agent_targets():
    """列出所有 Agent 管理的目标"""
    agent_host = request.args.get('agent_host', '')
    agent_id_filter = request.args.get('agent_id', '')
    
    all_targets = db.get_targets()
    
    # 过滤掉没有 agent_id 的目标（这些是 Server 直接监控的）
    agent_targets = [t for t in all_targets if t.get('agent_id')]
    
    # 如果指定了 Agent host 进行过滤
    if agent_host:
        config = load_config()
        # 找到对应的 Agent
        target_agent = None
        for a in config.get('agents', []):
            if a.get('host') == agent_host or a.get('agent_id') == agent_host:
                target_agent = a
                break
        if target_agent:
            agent_id = target_agent.get('agent_id', '')
            agent_targets = [t for t in agent_targets if t.get('agent_id') == agent_id]
    
    # 如果指定了 agent_id 进行过滤
    if agent_id_filter:
        agent_targets = [t for t in agent_targets if t.get('agent_id') == agent_id_filter]
    
    # 补充 Agent 信息
    config = load_config()
    agents_by_id = {a.get('agent_id'): a for a in config.get('agents', [])}
    agents_by_host = {a.get('host'): a for a in config.get('agents', [])}
    
    enriched_targets = []
    for t in agent_targets:
        # 优先用 agent_id 匹配
        agent = agents_by_id.get(t.get('agent_id'))
        # 如果没找到，尝试用 host 匹配
        if not agent and t.get('agent_host'):
            agent = agents_by_host.get(t.get('agent_host'))
        enriched_targets.append({
            **t,
            'agent_name': agent.get('name', '') if agent else '',
            'agent_host': agent.get('host', '') if agent else t.get('agent_host', ''),
            'agent_status': agent.get('status', 'offline') if agent else 'offline'
        })
    
    return jsonify({
        'status': 'success',
        'targets': enriched_targets
    })


@app.route('/api/v1/agent-targets/<target_id>', methods=['GET'])
def get_agent_target(target_id):
    """获取单个 Agent 目标"""
    agent_id = request.args.get('agent_id', '')
    
    target = db.get_target(target_id)
    if target and (not agent_id or target.get('agent_id') == agent_id):
        return jsonify({
            'status': 'success',
            'target': target
        })
    
    return jsonify({'error': 'Target not found'}), 404


@app.route('/api/v1/agent-targets', methods=['POST'])
def add_agent_target():
    """添加 Agent 目标"""
    data = request.json or {}
    
    if not data.get('url'):
        return jsonify({'error': 'url is required'}), 400
    
    if not data.get('agent_id'):
        return jsonify({'error': 'agent_id is required'}), 400
    
    # 验证 Agent 是否存在
    config = load_config()
    agents = config.get('agents', [])
    agent = None
    agent_id_input = data.get('agent_id')
    
    for a in agents:
        # 优先匹配 agent_id 字段
        if a.get('agent_id') == agent_id_input:
            agent = a
            break
        # 尝试匹配 host 字段
        if a.get('host') == agent_id_input:
            agent = a
            break
        # 尝试匹配 IP 字段
        if a.get('ip') == agent_id_input:
            agent = a
            break
        # 如果 Agent 名称包含输入值
        if agent_id_input and agent_id_input in str(a.get('name', '')):
            agent = a
            break
    
    if not agent:
        return jsonify({'error': f'Agent not found: {agent_id_input}'}), 404
    
    # 使用 Agent 的 agent_id 字段存储（这是唯一标识符）
    target_agent_id = agent.get('agent_id')
    
    target = {
        'id': data.get('id', str(time.time())),
        'url': data.get('url'),
        'service_name': data.get('service_name', data.get('url')),
        'owner': data.get('owner', ''),
        'owner_email': data.get('owner_email', ''),
        'env': data.get('env', 'production'),
        'agent_id': target_agent_id,  # 使用 Agent 的 agent_id
        'agent_host': agent.get('host', ''),  # 额外存储 host 便于显示
        'timeout': data.get('timeout', 30),
        'check_interval': data.get('check_interval', 180),
        'enabled': data.get('enabled', True),
        'created_at': datetime.now().isoformat()
    }
    
    db.upsert_target(target)
    save_targets_config()
    
    logger.info(f"添加 Agent 目标: {target['url']} -> {agent.get('name')} ({target_agent_id})")
    return jsonify({'status': 'success', 'target': target})


@app.route('/api/v1/agent-targets/<target_id>', methods=['PUT'])
def update_agent_target(target_id):
    """更新 Agent 目标"""
    data = request.json or {}
    
    # 如果指定了 agent_id，需要先验证 Agent 存在
    new_agent_id = data.get('agent_id')
    if new_agent_id:
        config = load_config()
        agent = None
        for a in config.get('agents', []):
            if a.get('agent_id') == new_agent_id or a.get('host') == new_agent_id:
                agent = a
                break
        if not agent:
            return jsonify({'error': f'Agent not found: {new_agent_id}'}), 404
        new_agent_id = agent.get('agent_id')  # 使用正确的 agent_id
        
    existing = db.get_target(target_id)
    if not existing:
        return jsonify({'error': 'Target not found'}), 404
    
    old_url = existing.get('url', '')
    new_url_value = data.get('url', existing.get('url'))
    
    updated = {
        **existing,
        'url': new_url_value,
        'service_name': data.get('service_name', existing.get('service_name')),
        'owner': data.get('owner', existing.get('owner', '')),
        'owner_email': data.get('owner_email', existing.get('owner_email', '')),
        'env': data.get('env', existing.get('env', 'production')),
        'agent_id': new_agent_id if new_agent_id else existing.get('agent_id'),
        'timeout': data.get('timeout', existing.get('timeout', 30)),
        'check_interval': data.get('check_interval', existing.get('check_interval', 180)),
        'enabled': data.get('enabled', existing.get('enabled', True))
    }
    db.upsert_target(updated)
    save_targets_config()
    
    # 如果 URL 发生变化或被禁用，清理关联的证书指标数据
    should_clean = False
    clean_target = dict(existing)
    
    if old_url != new_url_value:
        # URL 变更：清理旧 URL 的指标
        clean_target['url'] = old_url
        should_clean = True
    
    new_enabled = data.get('enabled')
    if new_enabled is not None and not new_enabled and existing.get('enabled', True):
        # 目标被禁用：清理指标
        clean_target['url'] = new_url_value
        should_clean = True
    
    if should_clean:
        _clean_metrics_for_target(clean_target)
    
    return jsonify({'status': 'success', 'target': updated})
    
    return jsonify({'error': 'Target not found'}), 404


@app.route('/api/v1/agent-targets/<target_id>', methods=['DELETE'])
def delete_agent_target(target_id):
    """删除 Agent 目标"""
    agent_id = request.args.get('agent_id', '')
    
    target = db.get_target(target_id)
    if target:
        if agent_id and target.get('agent_id') != agent_id:
            return jsonify({'error': 'Target not found'}), 404
        db.delete_target(target_id)
        save_targets_config()
        # 清理关联的证书指标数据（内存 + 数据库）
        _clean_metrics_for_target(target)
        logger.info(f"删除 Agent 目标: {target_id} (agent_id={agent_id or 'any'})")
        return jsonify({'status': 'success'})
    
    return jsonify({'error': 'Target not found'}), 404


# ========== 凭证管理 API ==========

CREDENTIAL_TYPE_LABELS = {
    'cert': 'SSL证书',
    'key': '密钥',
    'auth': '授权',
    'password': '口令/密码',
}


@app.route('/api/v1/credentials', methods=['GET'])
def list_credentials():
    """列出所有凭证，支持筛选"""
    cred_type = request.args.get('type', '')
    status = request.args.get('status', '')
    keyword = request.args.get('keyword', '')
    
    result = db.get_credentials()
    
    # 计算状态
    enriched = []
    for cred in result:
        info = compute_credential_status(cred.get('expiry_date', ''))
        enriched_cred = dict(cred)
        enriched_cred['status'] = info['status']
        enriched_cred['days_left'] = info['days_left']
        enriched_cred['level'] = info['level']
        enriched.append(enriched_cred)
    
    # 筛选
    if cred_type:
        enriched = [c for c in enriched if c.get('type') == cred_type]
    if status:
        enriched = [c for c in enriched if c.get('status') == status]
    if keyword:
        kw = keyword.lower()
        enriched = [c for c in enriched if kw in c.get('name', '').lower() or kw in c.get('owner', '').lower() or kw in c.get('remark', '').lower()]
    
    # 统计
    stats = {
        'total': len(enriched),
        'expired': len([c for c in enriched if c.get('status') == 'expired']),
        'critical': len([c for c in enriched if c.get('status') == 'critical']),
        'warning': len([c for c in enriched if c.get('status') == 'warning']),
        'normal': len([c for c in enriched if c.get('status') == 'normal']),
        'unknown': len([c for c in enriched if c.get('status') == 'unknown']),
    }
    
    return jsonify({
        'status': 'success',
        'credentials': enriched,
        'stats': stats,
    })


@app.route('/api/v1/credentials', methods=['POST'])
def add_credential():
    """添加凭证"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    name = data.get('name', '').strip()
    cred_type = data.get('type', '').strip()
    expiry_date = data.get('expiry_date', '').strip()
    
    if not name:
        return jsonify({'error': '凭证名称不能为空'}), 400
    if cred_type not in CREDENTIAL_TYPE_LABELS:
        return jsonify({'error': f'凭证类型无效，可选: {", ".join(CREDENTIAL_TYPE_LABELS.keys())}'}), 400
    
    credential = {
        'id': data.get('id') or '',
        'name': name,
        'type': cred_type,
        'type_label': CREDENTIAL_TYPE_LABELS.get(cred_type, cred_type),
        'expiry_date': expiry_date,
        'owner': data.get('owner', '').strip(),
        'owner_email': data.get('owner_email', '').strip(),
        'env': data.get('env', 'production'),
        'service_name': data.get('service_name', '').strip(),
        'remark': data.get('remark', '').strip(),
        'enabled': data.get('enabled', True),
        'notify_days': data.get('notify_days', CREDENTIAL_WARN_DAYS),
    }
    
    db.upsert_credential(credential)
    
    info = compute_credential_status(expiry_date)
    credential['status'] = info['status']
    credential['days_left'] = info['days_left']
    credential['level'] = info['level']
    
    logger.info(f"添加凭证: {name} ({cred_type}), 过期日期: {expiry_date}")
    return jsonify({'status': 'success', 'credential': credential}), 201


@app.route('/api/v1/credentials/<cred_id>', methods=['GET'])
def get_credential(cred_id):
    """获取单个凭证"""
    cred = db.get_credential(cred_id)
    if not cred:
        return jsonify({'error': 'Credential not found'}), 404
    
    result = dict(cred)
    info = compute_credential_status(cred.get('expiry_date', ''))
    result['status'] = info['status']
    result['days_left'] = info['days_left']
    result['level'] = info['level']
    return jsonify({'status': 'success', 'credential': result})


@app.route('/api/v1/credentials/<cred_id>', methods=['PUT'])
def update_credential(cred_id):
    """更新凭证"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    existing = db.get_credential(cred_id)
    if not existing:
        return jsonify({'error': 'Credential not found'}), 404
    
    if 'type' in data and data['type'] not in CREDENTIAL_TYPE_LABELS:
        return jsonify({'error': '凭证类型无效'}), 400
    
    updated = dict(existing)
    if 'name' in data:
        updated['name'] = data['name'].strip()
    if 'type' in data:
        updated['type'] = data['type']
        updated['type_label'] = CREDENTIAL_TYPE_LABELS.get(data['type'], data['type'])
    if 'expiry_date' in data:
        updated['expiry_date'] = data['expiry_date'].strip()
    if 'owner' in data:
        updated['owner'] = data['owner'].strip()
    if 'owner_email' in data:
        updated['owner_email'] = data['owner_email'].strip()
    if 'env' in data:
        updated['env'] = data['env']
    if 'service_name' in data:
        updated['service_name'] = data['service_name'].strip()
    if 'remark' in data:
        updated['remark'] = data['remark'].strip()
    if 'enabled' in data:
        updated['enabled'] = data['enabled']
    if 'notify_days' in data:
        updated['notify_days'] = data['notify_days']
    
    db.upsert_credential(updated)
    
    result = dict(updated)
    info = compute_credential_status(updated.get('expiry_date', ''))
    result['status'] = info['status']
    result['days_left'] = info['days_left']
    result['level'] = info['level']
    
    logger.info(f"更新凭证: {updated.get('name')} ({cred_id})")
    return jsonify({'status': 'success', 'credential': result})


@app.route('/api/v1/credentials/<cred_id>', methods=['DELETE'])
def delete_credential(cred_id):
    """删除凭证"""
    if db.delete_credential(cred_id):
        logger.info(f"删除凭证: {cred_id}")
        return jsonify({'status': 'success'})
    
    return jsonify({'error': 'Credential not found'}), 404


@app.route('/api/v1/credentials/check', methods=['POST'])
def check_credentials_now():
    """立即检查凭证过期状态"""
    credentials = db.get_credentials(enabled_only=True)
    alerts = []
    for cred in credentials:
        if not cred.get('expiry_date'):
            continue
        info = compute_credential_status(cred['expiry_date'])
        if info['status'] in ('critical', 'expired', 'warning'):
            alerts.append({
                'name': cred.get('name', ''),
                'type': cred.get('type', ''),
                'expiry_date': cred.get('expiry_date', ''),
                'days_left': info['days_left'],
                'level': info['level'],
                'owner': cred.get('owner', ''),
                'owner_email': cred.get('owner_email', ''),
            })
    
    if alerts:
        if CREDENTIAL_ALERT_WEBHOOK:
            try:
                send_feishu_credential_alert(alerts)
            except Exception as e:
                logger.error(f"手动触发飞书告警失败: {e}")
        if CREDENTIAL_ALERT_EMAILS:
            try:
                send_email_credential_alert(alerts)
            except Exception as e:
                logger.error(f"手动触发邮件告警失败: {e}")
    
    return jsonify({
        'status': 'success',
        'alerts_count': len(alerts),
        'alerts': alerts,
    })


@app.route('/api/v1/credentials/stats', methods=['GET'])
def get_credentials_stats():
    """获取凭证统计信息"""
    creds = db.get_credentials()
    
    stats = {
        'total': len(creds),
        'by_type': {},
        'by_status': {'expired': 0, 'critical': 0, 'warning': 0, 'normal': 0, 'unknown': 0},
    }
    
    for cred in creds:
        ctype = cred.get('type', 'unknown')
        stats['by_type'][ctype] = stats['by_type'].get(ctype, 0) + 1
        
        info = compute_credential_status(cred.get('expiry_date', ''))
        stats['by_status'][info['status']] = stats['by_status'].get(info['status'], 0) + 1
    
    return jsonify({'status': 'success', 'stats': stats})


if __name__ == '__main__':
    from werkzeug.serving import run_simple
    import ssl as ssl_module
    
    print("=" * 60)
    print("SSL Certificate Server")
    print("=" * 60)
    print("模式: Agent 推送（推荐） + Agent 拉取（兼容） + 直接检测")
    print(f"数据库: {db.DB_HOST}:{db.DB_PORT}/{db.DB_NAME}")
    print(f"心跳超时: {HEARTBEAT_TIMEOUT} 秒")
    
    # 初始化数据库
    db.init_db()
    print("数据库初始化完成")
    
    # SSL/HTTPS 配置
    HTTPS_PORT = int(os.getenv('SERVER_HTTPS_PORT', '8092'))  # HTTPS 专用端口
    if ENABLE_HTTPS:
        print(f"HTTPS: 已启用")
        print(f"  证书: {SSL_CERT_FILE}")
        print(f"  私钥: {SSL_KEY_FILE}")
        print(f"  HTTPS 端口: {HTTPS_PORT}")
    else:
        print("HTTPS: 未启用 (使用 HTTP)")
    
    print("=" * 60)
    print("注意: Server 不对外暴露端口，仅内网拉取 Agent 数据")
    print("=" * 60)
    
    # 启动定时拉取线程
    scrape_thread = threading.Thread(target=_scrape_loop, daemon=True)
    scrape_thread.start()
    
    # 启动定时指标清理线程
    def _metrics_cleanup_loop():
        while True:
            time.sleep(3600)  # 每小时清理一次
            try:
                db.cleanup_old_metrics()
            except Exception as e:
                logger.error(f"指标清理失败: {e}")
    
    cleanup_thread = threading.Thread(target=_metrics_cleanup_loop, daemon=True)
    cleanup_thread.start()
    
    # 加载凭证配置并启动过期检查线程
    credential_check_thread = threading.Thread(target=check_credential_expiry, daemon=True)
    credential_check_thread.start()
    print(f"凭证过期检查线程已启动 (间隔: {CREDENTIAL_ALERT_CHECK_INTERVAL}s, 预警: ≤{CREDENTIAL_WARN_DAYS}天, 高危: ≤{CREDENTIAL_CRIT_DAYS}天)")
    
    # Server 监听配置
    listen_host = os.getenv('SERVER_LISTEN_HOST', '0.0.0.0')
    listen_port = int(os.getenv('SERVER_LISTEN_PORT', '8090'))
    
    print(f"HTTP 监听: {listen_host}:{listen_port} (nginx 代理使用)")
    print(f"HTTPS 监听: {listen_host}:{HTTPS_PORT} (Agent 通信使用)")
    print("API Endpoints:")
    print("  - GET  /health                              - 健康检查")
    print("  - GET  /stats                               - 统计信息")
    print("  - GET  /metrics                             - Prometheus 指标")
    print("  - GET  /targets                             - Agent 目标管理页面")
    print("  - POST /api/v1/agents                       - 添加 Agent")
    print("  - GET  /api/v1/agents                       - 列出 Agent")
    print("  - POST /api/v1/agents/<id>/metrics          - [推送] 接收 Agent 指标")
    print("  - POST /api/v1/agents/<id>/heartbeat         - [推送] 接收 Agent 心跳")
    print("  - GET  /api/v1/agents/targets               - Agent 拉取目标配置")
    print("  - GET  /api/v1/agent-targets                - 列出 Agent 目标")
    print("  - POST /api/v1/agent-targets                - 添加 Agent 目标")
    print("  - PUT  /api/v1/agent-targets                - 更新 Agent 目标")
    print("  - DELETE /api/v1/agent-targets              - 删除 Agent 目标")
    print("  - POST /api/v1/scrape                       - 手动触发拉取")
    print("  - GET  /api/v1/credentials                  - 列出凭证")
    print("  - POST /api/v1/credentials                  - 添加凭证")
    print("  - PUT  /api/v1/credentials/<id>              - 更新凭证")
    print("  - DELETE /api/v1/credentials/<id>            - 删除凭证")
    print("  - POST /api/v1/credentials/check            - 立即检查过期")
    print("  - GET  /api/v1/credentials/stats            - 凭证统计")
    print("=" * 60)
    
    # 根据是否启用 HTTPS 配置 SSL
    ssl_context = None
    if ENABLE_HTTPS and os.path.exists(SSL_CERT_FILE) and os.path.exists(SSL_KEY_FILE):
        ssl_context = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(SSL_CERT_FILE, SSL_KEY_FILE)
        print(f"启动 HTTPS 服务器在端口 {HTTPS_PORT}...")
    
    # 启动 HTTP 服务器（始终启动，给 nginx 代理使用）
    print(f"启动 HTTP 服务器在端口 {listen_port}...")
    
    # 如果启用了 HTTPS，同时启动 HTTPS 服务器
    if ssl_context:
        # 使用线程同时启动 HTTP 和 HTTPS
        import socketserver
        from werkzeug.serving import make_server
        
        # HTTP 服务器
        http_server = make_server(listen_host, listen_port, app, threaded=True)
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()
        print(f"HTTP 服务器已启动: {listen_host}:{listen_port}")
        
        # HTTPS 服务器
        https_server = make_server(listen_host, HTTPS_PORT, app, threaded=True, ssl_context=ssl_context)
        https_thread = threading.Thread(target=https_server.serve_forever, daemon=True)
        https_thread.start()
        print(f"HTTPS 服务器已启动: {listen_host}:{HTTPS_PORT}")
        
        print("=" * 60)
        print("Server 已启动，按 Ctrl+C 停止")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在停止服务器...")
            http_server.shutdown()
            https_server.shutdown()
    else:
        # 只启动 HTTP 服务器
        app.run(
            host=listen_host,
            port=listen_port,
            debug=os.getenv('DEBUG', 'false').lower() == 'true',
            threaded=True
        )

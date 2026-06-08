#!/usr/bin/env python3
"""
AlertManager Email Alert Service
接收 AlertManager 告警，根据目标的 owner_email 发送邮件通知
"""

from flask import Flask, request, jsonify
import smtplib
import os
import json
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

app = Flask(__name__)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SMTP 配置（从环境变量获取）
SMTP_HOST = os.getenv('SMTP_HOST', '')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_FROM = os.getenv('SMTP_FROM', SMTP_USER)
SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'

# 配置文件路径 - 支持多个配置文件
CONFIG_PATHS = os.getenv('TARGETS_CONFIG_PATH', '/app/data/ssl_targets.json,/app/data/agent_targets.json').split(',')

# 凭证配置路径
CREDENTIALS_CONFIG_PATH = os.getenv('CREDENTIALS_CONFIG_PATH', '/app/data/credentials.json')


def load_targets_config():
    """加载目标配置（支持多个配置文件）"""
    combined = {'targets': []}
    seen_urls = set()
    
    for config_path in CONFIG_PATHS:
        config_path = config_path.strip()
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    targets = config.get('targets', [])
                    for target in targets:
                        url = target.get('url', '')
                        # 避免重复
                        if url not in seen_urls:
                            seen_urls.add(url)
                            combined['targets'].append(target)
                logger.info(f"已加载配置文件: {config_path}, 目标数: {len(targets)}")
        except Exception as e:
            logger.error(f"加载配置文件 {config_path} 失败: {e}")
    
    return combined


def load_credentials_config():
    """加载凭证配置"""
    try:
        if os.path.exists(CREDENTIALS_CONFIG_PATH):
            with open(CREDENTIALS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                credentials = data.get('credentials', [])
                logger.info(f"已加载凭证配置: {CREDENTIALS_CONFIG_PATH}, 凭证数: {len(credentials)}")
                return credentials
    except Exception as e:
        logger.error(f"加载凭证配置 {CREDENTIALS_CONFIG_PATH} 失败: {e}")
    return []


def build_targets_map(config):
    """构建 URL -> 目标信息 的映射"""
    targets_map = {}
    targets = config.get('targets', [])
    for target in targets:
        url = target.get('url', '')
        targets_map[url] = target
    return targets_map


def get_owner_email(alert):
    """从告警标签获取负责人邮箱"""
    labels = alert.get('labels', {})
    alert_type = labels.get('alert_type', '')
    
    # 凭证管理告警 - 直接使用标签中的 owner_email 或从凭证配置查找
    if alert_type == 'credential':
        # 优先使用标签中的 owner_email
        owner_email = labels.get('owner_email', '')
        if owner_email:
            return owner_email
        # 从凭证配置查找
        credential_name = labels.get('credential_name', '')
        if credential_name:
            credentials = load_credentials_config()
            for cred in credentials:
                if cred.get('name', '') == credential_name:
                    email = cred.get('owner_email', '')
                    if email:
                        return email
        return None
    
    # SSL 证书告警 - 原有逻辑
    
    # 优先使用 target_url 标签（已通过 metric_relabel_configs 设置）
    target_url = labels.get('target_url', '')
    if target_url:
        config = load_targets_config()
        targets_map = build_targets_map(config)
        # 直接匹配 target_url
        target = targets_map.get(target_url)
        if target:
            email = target.get('owner_email', '')
            if email:
                return email
        
        # 尝试匹配去掉协议前缀的 URL
        url_without_scheme = target_url
        if target_url.startswith('https://'):
            url_without_scheme = target_url[8:]
        elif target_url.startswith('http://'):
            url_without_scheme = target_url[7:]
        
        # 去掉路径部分
        url_without_path = url_without_scheme.split('/')[0]
        
        # 遍历匹配
        for url, target in targets_map.items():
            config_url = url
            if config_url.startswith('https://'):
                config_url = config_url[8:]
            elif config_url.startswith('http://'):
                config_url = config_url[7:]
            config_url = config_url.split('/')[0]
            
            if config_url == url_without_path:
                email = target.get('owner_email', '')
                if email:
                    return email
    
    # 备用方案：使用 hostname 和 port 标签
    hostname = labels.get('hostname', '')
    port = labels.get('port', '443')
    
    if not hostname:
        # 最后尝试使用 instance 标签（可能是 exporter 地址）
        instance = labels.get('instance', '')
        if instance and instance != 'ssl-custom-exporter:9116':
            parts = instance.split(':')
            hostname = parts[0] if parts else ''
            port = parts[1] if len(parts) > 1 else '443'
    
    if hostname:
        config = load_targets_config()
        targets_map = build_targets_map(config)
        
        for url, target in targets_map.items():
            target_url = url
            if target_url.startswith('https://'):
                target_url = target_url[8:]
            elif target_url.startswith('http://'):
                target_url = target_url[7:]
            
            target_parts = target_url.split('/')[0].split(':')
            target_hostname = target_parts[0]
            target_port = target_parts[1] if len(target_parts) > 1 else '443'
            
            if hostname == target_hostname and port == target_port:
                email = target.get('owner_email', '')
                if email:
                    return email
            
            if hostname == target_hostname:
                email = target.get('owner_email', '')
                if email:
                    return email
    
    return None


def send_email(to_email, subject, html_body):
    """发送邮件"""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP 配置不完整")
        return False, "SMTP 配置不完整"
    
    if not to_email:
        return False, "收件人邮箱为空"
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        
        # 添加 HTML 内容
        html_part = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(html_part)
        
        # 发送邮件
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        
        logger.info(f"邮件发送成功: {to_email}")
        return True, "邮件发送成功"
    
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP 认证失败，请检查用户名和密码"
    except smtplib.SMTPException as e:
        logger.error(f"SMTP 错误: {e}")
        return False, f"SMTP 错误: {str(e)}"
    except Exception as e:
        logger.error(f"发送邮件失败: {e}")
        return False, f"发送邮件失败: {str(e)}"


def build_email_content(alerts, status='firing'):
    """构建邮件内容 - 根据 alert_type 区分 SSL 证书告警和凭证管理告警"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 检查是否为凭证管理告警
    is_credential = any(a.get('labels', {}).get('alert_type') == 'credential' for a in alerts)
    
    if is_credential:
        return build_credential_email_content(alerts, status, now)
    
    # ========== SSL 证书告警（原有逻辑） ==========
    # 统计告警严重程度
    critical_count = sum(1 for a in alerts if a.get('labels', {}).get('severity') == 'critical')
    warning_count = sum(1 for a in alerts if a.get('labels', {}).get('severity') == 'warning')
    
    alert_rows = ""
    for alert in alerts:
        labels = alert.get('labels', {})
        annotations = alert.get('annotations', {})
        
        alertname = labels.get('alertname', '未知告警')
        severity = labels.get('severity', 'warning')
        # 优先使用 target_url，其次使用 hostname:port，最后使用 instance
        target_url = labels.get('target_url', '')
        hostname = labels.get('hostname', '')
        port = labels.get('port', '443')
        target_addr = target_url if target_url else f"{hostname}:{port}" if hostname else labels.get('instance', 'N/A')
        service_name = labels.get('service_name', labels.get('job', 'N/A'))
        owner = labels.get('owner', '未知')
        # 尝试从多个位置获取 description
        description = annotations.get('description', '')
        if not description:
            description = labels.get('description', '')
        if not description:
            description = annotations.get('summary', '无详细信息')
        starts_at = alert.get('startsAt', '')
        
        # 格式化时间
        try:
            starts_dt = datetime.strptime(starts_at.replace('Z', '+0000'), '%Y-%m-%dT%H:%M:%S+0000')
            starts_at = starts_dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass
        
        # 根据严重程度设置颜色
        severity_color = '#dc2626' if severity == 'critical' else '#f59e0b'
        severity_bg = '#fef2f2' if severity == 'critical' else '#fffbeb'
        status_text = '🔴 告警中' if status == 'firing' else '✅ 已恢复'
        
        # 提取证书剩余天数（从 description 中解析）
        days_left = ''
        import re
        match = re.search(r'(\d+\.?\d*)\s*天', description)
        if match:
            days = float(match.group(1))
            if days < 7:
                days_left = f'<span style="color: #dc2626; font-weight: bold;">{days:.1f} 天</span>'
            elif days < 30:
                days_left = f'<span style="color: #f59e0b; font-weight: bold;">{days:.1f} 天</span>'
            else:
                days_left = f'{days:.1f} 天'
        
        alert_rows += f"""
        <tr style="background: {severity_bg};">
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{alertname}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">
                <span style="color: {severity_color}; font-weight: bold;">{severity.upper()}</span>
            </td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{status_text}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{service_name}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;"><a href="{target_url}">{target_addr}</a></td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{days_left}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{owner}</td>
        </tr>
        """
    
    # 告警详情卡片
    alert_details = ""
    for alert in alerts:
        labels = alert.get('labels', {})
        annotations = alert.get('annotations', {})
        target_url = labels.get('target_url', '')
        hostname = labels.get('hostname', '')
        port = labels.get('port', '443')
        target_addr = target_url if target_url else f"{hostname}:{port}" if hostname else labels.get('instance', 'N/A')
        service_name = labels.get('service_name', '未知')
        owner = labels.get('owner', '未知')
        description = annotations.get('description', '')
        if not description:
            description = labels.get('description', '')
        if not description:
            description = annotations.get('summary', '无详细信息')
        severity = labels.get('severity', 'warning')
        
        severity_color = '#dc2626' if severity == 'critical' else '#f59e0b'
        severity_icon = '🔴' if severity == 'critical' else '⚠️'
        
        alert_details += f"""
                <div style="margin-bottom: 20px; padding: 15px; background: white; border-radius: 8px; border-left: 4px solid {severity_color}; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h4 style="margin: 0; color: #333;">{severity_icon} {service_name}</h4>
                        <span style="background: {severity_color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px;">{severity.upper()}</span>
                    </div>
                    <p style="margin: 5px 0; color: #666; font-size: 14px;">
                        <strong>目标地址:</strong> <a href="{target_url}">{target_addr}</a>
                    </p>
                    <p style="margin: 5px 0; color: #666; font-size: 14px;">
                        <strong>负责人:</strong> {owner}
                    </p>
                    <p style="margin: 10px 0; padding: 10px; background: #f3f4f6; border-radius: 4px; font-size: 14px; color: #333;">
                        {description}
                    </p>
                </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; background: #f0f2f5; }}
            .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #dc2626 0%, #f59e0b 100%); color: white; padding: 25px 30px; border-radius: 12px 12px 0 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .header h1 {{ margin: 0 0 10px 0; font-size: 24px; display: flex; align-items: center; gap: 10px; }}
            .header p {{ margin: 0; opacity: 0.9; font-size: 14px; }}
            .content {{ background: white; padding: 25px 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .stats {{ display: flex; gap: 20px; margin-bottom: 25px; }}
            .stat-card {{ flex: 1; padding: 15px; border-radius: 8px; text-align: center; }}
            .stat-critical {{ background: #fef2f2; border: 1px solid #fecaca; }}
            .stat-warning {{ background: #fffbeb; border: 1px solid #fde68a; }}
            .stat-total {{ background: #f0f9ff; border: 1px solid #bae6fd; }}
            .stat-number {{ font-size: 28px; font-weight: bold; }}
            .stat-label {{ font-size: 12px; color: #666; margin-top: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            th {{ background: #1f2937; color: white; padding: 12px; text-align: left; font-weight: 500; font-size: 13px; }}
            td {{ padding: 12px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }}
            tr:last-child td {{ border-bottom: none; }}
            .footer {{ margin-top: 25px; text-align: center; color: #9ca3af; font-size: 12px; padding-top: 20px; border-top: 1px solid #e5e7eb; }}
            .action-btn {{ display: inline-block; background: #3b82f6; color: white; padding: 10px 20px; border-radius: 6px; text-decoration: none; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔔 SSL 证书告警通知</h1>
                <p>通知时间: {now}</p>
            </div>
            <div class="content">
                <div class="stats">
                    <div class="stat-card stat-critical">
                        <div class="stat-number" style="color: #dc2626;">{critical_count}</div>
                        <div class="stat-label">严重告警</div>
                    </div>
                    <div class="stat-card stat-warning">
                        <div class="stat-number" style="color: #f59e0b;">{warning_count}</div>
                        <div class="stat-label">警告告警</div>
                    </div>
                    <div class="stat-card stat-total">
                        <div class="stat-number" style="color: #3b82f6;">{len(alerts)}</div>
                        <div class="stat-label">总计告警</div>
                    </div>
                </div>
                
                <h3 style="margin-bottom: 15px; color: #1f2937; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px;">📋 告警列表</h3>
                <table>
                    <thead>
                        <tr>
                            <th>告警名称</th>
                            <th>严重程度</th>
                            <th>状态</th>
                            <th>服务</th>
                            <th>目标地址</th>
                            <th>剩余天数</th>
                            <th>负责人</th>
                        </tr>
                    </thead>
                    <tbody>
                        {alert_rows}
                    </tbody>
                </table>
                
                <h3 style="margin: 25px 0 15px 0; color: #1f2937; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px;">📝 告警详情</h3>
                <div style="margin-bottom: 20px;">
                    {alert_details}
                </div>
                
                <div style="text-align: center; margin-top: 20px;">
                    <p style="color: #666; font-size: 14px;">请及时处理以上告警，确保 SSL 证书正常工作</p>
                </div>
            </div>
            <div class="footer">
                <p>由 SSL Certificate Monitoring System 自动发送</p>
                <p>如需管理告警规则或目标配置，请访问监控系统管理后台</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


def build_credential_email_content(alerts, status='firing', now=None):
    """构建凭证管理告警邮件内容"""
    if not now:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    critical_count = sum(1 for a in alerts if a.get('labels', {}).get('severity') == 'critical')
    warning_count = sum(1 for a in alerts if a.get('labels', {}).get('severity') == 'warning')
    
    # 凭证类型映射
    type_labels = {
        'cert': 'SSL证书', 'key': '密钥', 'auth': '授权', 'password': '口令/密码'
    }
    
    alert_rows = ""
    for alert in alerts:
        labels = alert.get('labels', {})
        annotations = alert.get('annotations', {})
        
        alertname = labels.get('alertname', '未知告警')
        severity = labels.get('severity', 'warning')
        credential_name = labels.get('credential_name', '未知')
        credential_type = type_labels.get(labels.get('credential_type', ''), labels.get('credential_type', '未知'))
        service_name = labels.get('service_name', '未知')
        owner = labels.get('owner', '未知')
        env = labels.get('env', '未知')
        description = annotations.get('description', '')
        if not description:
            description = annotations.get('summary', '无详细信息')
        
        severity_color = '#dc2626' if severity == 'critical' else '#f59e0b'
        severity_bg = '#fef2f2' if severity == 'critical' else '#fffbeb'
        status_text = '🔴 告警中' if status == 'firing' else '✅ 已恢复'
        
        # 提取剩余天数
        import re
        days_left = ''
        match = re.search(r'(\d+\.?\d*)\s*天', description)
        if match:
            days = float(match.group(1))
            if days < 0:
                days_left = f'<span style="color: #dc2626; font-weight: bold;">已过期 {-days:.0f} 天</span>'
            elif days < 7:
                days_left = f'<span style="color: #dc2626; font-weight: bold;">{days:.1f} 天</span>'
            elif days < 30:
                days_left = f'<span style="color: #f59e0b; font-weight: bold;">{days:.1f} 天</span>'
            else:
                days_left = f'{days:.1f} 天'
        
        alert_rows += f"""
        <tr style="background: {severity_bg};">
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{alertname}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">
                <span style="color: {severity_color}; font-weight: bold;">{severity.upper()}</span>
            </td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{status_text}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{credential_name}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{credential_type}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{days_left}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{service_name}</td>
            <td style="padding: 12px; border: 1px solid #e5e7eb;">{owner}</td>
        </tr>
        """
    
    # 告警详情卡片
    alert_details = ""
    for alert in alerts:
        labels = alert.get('labels', {})
        annotations = alert.get('annotations', {})
        credential_name = labels.get('credential_name', '未知')
        credential_type = type_labels.get(labels.get('credential_type', ''), labels.get('credential_type', '未知'))
        service_name = labels.get('service_name', '未知')
        owner = labels.get('owner', '未知')
        env = labels.get('env', '未知')
        description = annotations.get('description', '')
        if not description:
            description = annotations.get('summary', '无详细信息')
        severity = labels.get('severity', 'warning')
        
        severity_color = '#dc2626' if severity == 'critical' else '#f59e0b'
        severity_icon = '🔴' if severity == 'critical' else '⚠️'
        
        alert_details += f"""
                <div style="margin-bottom: 20px; padding: 15px; background: white; border-radius: 8px; border-left: 4px solid {severity_color}; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h4 style="margin: 0; color: #333;">{severity_icon} {credential_name}</h4>
                        <span style="background: {severity_color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px;">{severity.upper()}</span>
                    </div>
                    <p style="margin: 5px 0; color: #666; font-size: 14px;">
                        <strong>凭证类型:</strong> {credential_type}
                    </p>
                    <p style="margin: 5px 0; color: #666; font-size: 14px;">
                        <strong>所属服务:</strong> {service_name}
                    </p>
                    <p style="margin: 5px 0; color: #666; font-size: 14px;">
                        <strong>环境:</strong> {env}
                    </p>
                    <p style="margin: 5px 0; color: #666; font-size: 14px;">
                        <strong>负责人:</strong> {owner}
                    </p>
                    <p style="margin: 10px 0; padding: 10px; background: #f3f4f6; border-radius: 4px; font-size: 14px; color: #333;">
                        {description}
                    </p>
                </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; background: #f0f2f5; }}
            .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #7c3aed 0%, #2563eb 100%); color: white; padding: 25px 30px; border-radius: 12px 12px 0 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .header h1 {{ margin: 0 0 10px 0; font-size: 24px; display: flex; align-items: center; gap: 10px; }}
            .header p {{ margin: 0; opacity: 0.9; font-size: 14px; }}
            .content {{ background: white; padding: 25px 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .stats {{ display: flex; gap: 20px; margin-bottom: 25px; }}
            .stat-card {{ flex: 1; padding: 15px; border-radius: 8px; text-align: center; }}
            .stat-critical {{ background: #fef2f2; border: 1px solid #fecaca; }}
            .stat-warning {{ background: #fffbeb; border: 1px solid #fde68a; }}
            .stat-total {{ background: #f0f9ff; border: 1px solid #bae6fd; }}
            .stat-number {{ font-size: 28px; font-weight: bold; }}
            .stat-label {{ font-size: 12px; color: #666; margin-top: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            th {{ background: #1f2937; color: white; padding: 12px; text-align: left; font-weight: 500; font-size: 13px; }}
            td {{ padding: 12px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }}
            tr:last-child td {{ border-bottom: none; }}
            .footer {{ margin-top: 25px; text-align: center; color: #9ca3af; font-size: 12px; padding-top: 20px; border-top: 1px solid #e5e7eb; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔐 凭证管理告警通知</h1>
                <p>通知时间: {now}</p>
            </div>
            <div class="content">
                <div class="stats">
                    <div class="stat-card stat-critical">
                        <div class="stat-number" style="color: #dc2626;">{critical_count}</div>
                        <div class="stat-label">严重告警</div>
                    </div>
                    <div class="stat-card stat-warning">
                        <div class="stat-number" style="color: #f59e0b;">{warning_count}</div>
                        <div class="stat-label">警告告警</div>
                    </div>
                    <div class="stat-card stat-total">
                        <div class="stat-number" style="color: #3b82f6;">{len(alerts)}</div>
                        <div class="stat-label">总计告警</div>
                    </div>
                </div>
                
                <h3 style="margin-bottom: 15px; color: #1f2937; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px;">📋 凭证告警列表</h3>
                <table>
                    <thead>
                        <tr>
                            <th>告警名称</th>
                            <th>严重程度</th>
                            <th>状态</th>
                            <th>凭证名称</th>
                            <th>凭证类型</th>
                            <th>剩余天数</th>
                            <th>所属服务</th>
                            <th>负责人</th>
                        </tr>
                    </thead>
                    <tbody>
                        {alert_rows}
                    </tbody>
                </table>
                
                <h3 style="margin: 25px 0 15px 0; color: #1f2937; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px;">📝 告警详情</h3>
                <div style="margin-bottom: 20px;">
                    {alert_details}
                </div>
                
                <div style="text-align: center; margin-top: 20px;">
                    <p style="color: #666; font-size: 14px;">请及时处理以上凭证告警，确保相关凭证正常可用</p>
                </div>
            </div>
            <div class="footer">
                <p>由 SSL Certificate Monitoring System 自动发送</p>
                <p>如需管理告警规则或凭证配置，请访问监控系统管理后台</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


def group_alerts_by_email(alerts):
    """按负责人邮箱分组告警"""
    groups = {}
    
    for alert in alerts:
        email = get_owner_email(alert)
        if not email:
            # 使用默认邮箱或跳过
            email = 'default'
        
        if email not in groups:
            groups[email] = []
        groups[email].append(alert)
    
    return groups


@app.route('/webhook', methods=['POST'])
def receive_alertmanager_webhook():
    """
    接收 AlertManager 的 Webhook 请求
    """
    try:
        data = request.json
        
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data received'
            }), 400
        
        alerts = data.get('alerts', [])
        
        if not alerts:
            return jsonify({
                'status': 'success',
                'message': 'No alerts in payload'
            })
        
        # 按邮箱分组
        email_groups = group_alerts_by_email(alerts)
        
        results = []
        for email, email_alerts in email_groups.items():
            status = 'firing'
            if email_alerts and email_alerts[0].get('status') == 'resolved':
                status = 'resolved'
            
            is_credential = any(a.get('labels', {}).get('alert_type') == 'credential' for a in email_alerts)
            if is_credential:
                subject = f"🔐 凭证管理告警 - {len(email_alerts)}条" if status == 'firing' else f"✅ 凭证管理告警恢复 - {len(email_alerts)}条"
            else:
                subject = f"🔔 SSL证书告警 - {len(email_alerts)}条" if status == 'firing' else f"✅ SSL证书告警恢复 - {len(email_alerts)}条"
            html_body = build_email_content(email_alerts, status)
            
            if email == 'default':
                # 没有配置邮箱的告警，记录日志
                logger.warning(f"告警无对应负责人邮箱: {[a.get('labels', {}).get('instance') for a in email_alerts]}")
                results.append({
                    'email': email,
                    'status': 'skipped',
                    'message': '无负责人邮箱配置'
                })
            else:
                success, msg = send_email(email, subject, html_body)
                results.append({
                    'email': email,
                    'status': 'success' if success else 'error',
                    'message': msg
                })
        
        # 返回结果
        success_count = len([r for r in results if r['status'] == 'success'])
        return jsonify({
            'status': 'success',
            'message': f'处理完成，成功发送 {success_count}/{len(results)} 封邮件',
            'results': results
        })
    
    except Exception as e:
        logger.error(f"处理告警失败: {e}")
        return jsonify({
            'status': 'error',
            'message': f'处理告警失败: {str(e)}'
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': 'email-alert-service',
        'smtp_configured': bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)
    })


@app.route('/test', methods=['GET', 'POST'])
def test_email():
    """测试邮件发送"""
    test_email_addr = request.args.get('to') or os.getenv('TEST_EMAIL', '')
    
    if not test_email_addr:
        return jsonify({
            'status': 'error',
            'message': '请提供测试邮箱地址 ?to=email@example.com'
        }), 400
    
    subject = "🔔 SSL证书监控系统 - 测试邮件"
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body>
        <h2>这是一封测试邮件</h2>
        <p>如果您收到此邮件，说明 SSL 证书监控系统的邮件告警功能配置正确！</p>
        <p>发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </body>
    </html>
    """
    
    success, msg = send_email(test_email_addr, subject, html_body)
    
    return jsonify({
        'status': 'success' if success else 'error',
        'message': msg,
        'to': test_email_addr
    })


if __name__ == '__main__':
    print("=" * 60)
    print("AlertManager Email Alert Service")
    print("=" * 60)
    print(f"SMTP Host: {SMTP_HOST or '未配置'}")
    print(f"SMTP Port: {SMTP_PORT}")
    print(f"SMTP User: {SMTP_USER or '未配置'}")
    print(f"SMTP From: {SMTP_FROM}")
    print(f"Config Path: {CONFIG_PATH}")
    print("=" * 60)
    print("端点:")
    print("  - POST /webhook   - 接收 AlertManager 告警")
    print("  - GET  /health   - 健康检查")
    print("  - GET  /test     - 测试邮件发送")
    print("=" * 60)
    
    # 检查 SMTP 配置
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        print("⚠️ 警告: SMTP 配置不完整，请在环境变量中设置 SMTP_HOST, SMTP_USER, SMTP_PASSWORD")
    
    app.run(
        host='0.0.0.0',
        port=8080,
        debug=os.getenv('DEBUG', 'false').lower() == 'true'
    )

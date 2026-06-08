#!/usr/bin/env python3
"""
AlertManager to Feishu (Lark) Webhook Converter
将 AlertManager 的告警格式转换为飞书机器人支持的消息格式
"""

from flask import Flask, request, jsonify
import requests
import os
import json
from datetime import datetime

app = Flask(__name__)

# 飞书 Webhook URL（从环境变量获取）
FEISHU_WEBHOOK_URL = os.getenv('FEISHU_WEBHOOK_URL', '')

# 检查是否配置了 Webhook URL
if not FEISHU_WEBHOOK_URL:
    raise ValueError("错误: 未配置 FEISHU_WEBHOOK_URL 环境变量！请在 docker-compose.yml 中设置。")

# 是否发送恢复通知
SEND_RESOLVED = os.getenv('SEND_RESOLVED', 'true').lower() == 'true'


def format_timestamp(timestamp):
    """将时间戳转换为可读格式"""
    if timestamp:
        try:
            dt = datetime.fromtimestamp(int(timestamp))
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            return str(timestamp)
    return 'N/A'


def build_feishu_message(alert_data):
    """
    构建飞书消息内容
    
    Args:
        alert_data: AlertManager 发送的单条告警数据
    
    Returns:
        飞书消息文本
    """
    # 提取告警信息
    labels = alert_data.get('labels', {})
    annotations = alert_data.get('annotations', {})
    
    alert_name = labels.get('alertname', 'Unknown Alert')
    status = alert_data.get('status', 'unknown')
    severity = labels.get('severity', 'unknown')
    alert_type = labels.get('alert_type', '')
    instance = labels.get('instance', 'N/A')
    job = labels.get('job', 'N/A')
    
    # 告警内容
    summary = annotations.get('summary', '')
    description = annotations.get('description', '')
    message = annotations.get('message', description or summary)
    
    # 时间
    starts_at = format_timestamp(alert_data.get('startsAt', ''))
    
    # 构建消息
    msg_parts = []
    
    # 告警状态图标和标题
    if status == 'firing':
        status_icon = '🔴'
        status_text = '告警触发'
    elif status == 'resolved':
        status_icon = '✅'
        status_text = '告警恢复'
    else:
        status_icon = '⚠️'
        status_text = '未知状态'
    
    # 严重程度图标
    severity_icon = {
        'critical': '🚨',
        'warning': '⚠️',
        'info': 'ℹ️'
    }.get(severity.lower(), '📢')
    
    # 根据告警类型构建不同消息格式
    if alert_type == 'credential':
        # ========== 凭证管理告警 ==========
        type_labels = {'cert': 'SSL证书', 'key': '密钥', 'auth': '授权', 'password': '口令/密码'}
        credential_name = labels.get('credential_name', '未知')
        credential_type = type_labels.get(labels.get('credential_type', ''), labels.get('credential_type', '未知'))
        service_name = labels.get('service_name', '未知')
        owner = labels.get('owner', '未知')
        env = labels.get('env', '未知')
        
        msg_parts.append(f"{status_icon} **{status_text}** {severity_icon}")
        msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━")
        msg_parts.append(f"**告警名称**: {summary or alert_name}")
        msg_parts.append(f"**严重程度**: {severity.upper()}")
        msg_parts.append(f"**触发时间**: {starts_at}")
        msg_parts.append(f"**凭证名称**: {credential_name}")
        msg_parts.append(f"**凭证类型**: {credential_type}")
        msg_parts.append(f"**所属服务**: {service_name}")
        msg_parts.append(f"**环境**: {env}")
        msg_parts.append(f"**负责人**: {owner}")
        
        if message:
            msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━")
            msg_parts.append(f"**告警内容**:")
            msg_parts.append(f"{message}")
        
        msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━")
        msg_parts.append(f"_< 这是一条由凭证管理系统自动发送的告警 >_")
    else:
        # ========== SSL 证书告警（原有逻辑） ==========
        msg_parts.append(f"{status_icon} **{status_text}** {severity_icon}")
        msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━")
        
        if summary:
            msg_parts.append(f"**告警名称**: {summary}")
        else:
            msg_parts.append(f"**告警名称**: {alert_name}")
        
        msg_parts.append(f"**严重程度**: {severity.upper()}")
        msg_parts.append(f"**触发时间**: {starts_at}")
        
        if instance != 'N/A':
            msg_parts.append(f"**实例**: {instance}")
        
        if job != 'N/A':
            msg_parts.append(f"**监控任务**: {job}")
        
        # 告警描述
        if message:
            msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━")
            msg_parts.append(f"**告警内容**:")
            msg_parts.append(f"{message}")
        
        # 结束标记
        msg_parts.append(f"━━━━━━━━━━━━━━━━━━━━")
        msg_parts.append(f"_< 这是一条由 SSL 证书监控系统自动发送的告警 >_")
    
    return '\n'.join(msg_parts)


def build_feishu_text_message(alerts):
    """
    构建完整的飞书文本消息
    
    Args:
        alerts: AlertManager 发送的所有告警列表
    
    Returns:
        飞书消息字典
    """
    messages = []
    
    for alert in alerts:
        msg = build_feishu_message(alert)
        messages.append(msg)
    
    # 合并多条告警
    full_message = '\n\n'.join(messages)
    
    # 限制消息长度（飞书文本消息限制）
    if len(full_message) > 4000:
        full_message = full_message[:3997] + '...'
    
    return {
        "msg_type": "text",
        "content": {
            "text": full_message
        }
    }


def send_to_feishu(payload):
    """
    发送消息到飞书
    
    Args:
        payload: 飞书消息载荷
    
    Returns:
        (success: bool, message: str)
    """
    try:
        headers = {
            'Content-Type': 'application/json'
        }
        
        response = requests.post(
            FEISHU_WEBHOOK_URL,
            headers=headers,
            data=json.dumps(payload),
            timeout=10
        )
        
        result = response.json()
        
        if response.status_code == 200 and result.get('code') == 0:
            return True, "消息发送成功"
        else:
            return False, f"发送失败: {result.get('msg', 'Unknown error')}"
    
    except requests.exceptions.Timeout:
        return False, "发送超时（超过10秒）"
    except requests.exceptions.RequestException as e:
        return False, f"请求异常: {str(e)}"
    except Exception as e:
        return False, f"未知错误: {str(e)}"


@app.route('/webhook', methods=['POST'])
@app.route('/alert', methods=['POST'])
def receive_alertmanager_webhook():
    """
    接收 AlertManager 的 Webhook 请求
    
    期望的 AlertManager 发送的数据格式:
    {
        "version": "4",
        "groupKey": "...",
        "status": "firing" | "resolved",
        "alerts": [
            {
                "status": "firing" | "resolved",
                "labels": {...},
                "annotations": {...},
                "startsAt": "2024-01-01T00:00:00Z",
                "endsAt": "...",
                ...
            }
        ]
    }
    """
    try:
        data = request.json
        
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data received'
            }), 400
        
        # 提取告警列表
        alerts = data.get('alerts', [])
        
        if not alerts:
            return jsonify({
                'status': 'error',
                'message': 'No alerts in payload'
            }), 400
        
        # 获取全局状态
        global_status = data.get('status', 'unknown')
        
        # 过滤告警（根据配置决定是否发送恢复通知）
        alerts_to_send = []
        
        for alert in alerts:
            alert_status = alert.get('status', 'firing')
            
            # 如果配置为不发送恢复通知，则跳过 resolved 状态的告警
            if not SEND_RESOLVED and alert_status == 'resolved':
                continue
            
            alerts_to_send.append(alert)
        
        if not alerts_to_send:
            return jsonify({
                'status': 'success',
                'message': 'No alerts to send (all filtered)'
            })
        
        # 构建飞书消息
        feishu_message = build_feishu_text_message(alerts_to_send)
        
        # 发送到飞书
        success, msg = send_to_feishu(feishu_message)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': msg,
                'alerts_count': len(alerts_to_send)
            })
        else:
            return jsonify({
                'status': 'error',
                'message': msg,
                'alerts_count': len(alerts_to_send)
            }), 500
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'处理告警时出错: {str(e)}'
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'service': 'alertmanager-feishu-webhook'
    })


@app.route('/test', methods=['GET', 'POST'])
def test_feishu():
    """测试飞书连接"""
    test_message = {
        "msg_type": "text",
        "content": {
            "text": "🧪 **SSL 证书监控系统测试消息**\n\n这是一条来自 AlertManager-Feishu 转换服务的测试消息。\n\n如果收到此消息，说明配置正确！\n\n⏰ 测试时间: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    }
    
    success, msg = send_to_feishu(test_message)
    
    return jsonify({
        'status': 'success' if success else 'error',
        'message': msg
    })


if __name__ == '__main__':
    print("=" * 60)
    print("AlertManager to Feishu Webhook Converter")
    print("=" * 60)
    print(f"飞书 Webhook URL: {'*' * 8}{FEISHU_WEBHOOK_URL[-12:] if FEISHU_WEBHOOK_URL else '(未配置)'}")
    print(f"发送恢复通知: {SEND_RESOLVED}")
    print("=" * 60)
    print("服务地址: http://0.0.0.0:8080")
    print("端点:")
    print("  - POST /webhook  - 接收 AlertManager 告警")
    print("  - POST /alert    - 接收 AlertManager 告警（别名）")
    print("  - GET  /health   - 健康检查")
    print("  - GET  /test     - 测试飞书连接")
    print("=" * 60)
    
    # 启动服务
    app.run(
        host='0.0.0.0',
        port=8080,
        debug=os.getenv('DEBUG', 'false').lower() == 'true'
    )

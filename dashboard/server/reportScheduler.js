import cron from 'node-cron';
import nodemailer from 'nodemailer';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const CONFIG_PATH = process.env.TARGETS_CONFIG_PATH || '/app/data/ssl_targets.json';

// 内部服务地址（Docker 网络内部通信，不经过 Nginx）
const AGENT_SERVER_URL = process.env.AGENT_SERVER_INTERNAL_URL || 'http://ssl-agent-server:8090';
const EXPORTER_URL = process.env.EXPORTER_INTERNAL_URL || 'http://ssl-custom-exporter:9116';

// 调度器状态
let scheduledTask = null;
let lastSendResult = null;

// ==================== 配置读写 ====================

function readConfig() {
  try {
    if (!fs.existsSync(CONFIG_PATH)) {
      return { targets: [], settings: {} };
    }
    const data = fs.readFileSync(CONFIG_PATH, 'utf-8');
    return JSON.parse(data);
  } catch (error) {
    console.error('Error reading config:', error);
    return { targets: [], settings: {} };
  }
}

function writeConfig(config) {
  try {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf-8');
    return true;
  } catch (error) {
    console.error('Error writing config:', error);
    return false;
  }
}

function getReportScheduleConfig() {
  const config = readConfig();
  return config.settings?.report_schedule || {
    enabled: false,
    frequency: 'daily',
    cron_expression: '0 9 * * *',
    admin_emails: [],
    last_sent_at: null,
    last_send_status: null,
  };
}

function setReportScheduleConfig(scheduleConfig) {
  const config = readConfig();
  if (!config.settings) config.settings = {};
  config.settings.report_schedule = {
    ...config.settings.report_schedule,
    ...scheduleConfig,
  };
  return writeConfig(config);
}

// ==================== Metrics 解析 ====================

function parseMetrics(text) {
  const lines = text.split('\n');
  const metrics = new Map();
  const values = new Map();

  for (const line of lines) {
    if (line.startsWith('#') || !line.trim()) continue;

    try {
      const match = line.match(/^(\w+)\{(.+)\}\s+([\d.eE+-]+)$/);
      if (!match) continue;

      const [, metricName, labelsStr, valueStr] = match;
      const labels = {};

      const labelMatches = labelsStr.matchAll(/(\w+)="((?:[^"\\]|\\.)*)"/g);
      for (const [, key, value] of labelMatches) {
        labels[key] = value.replace(/\\"/g, '"').replace(/\\\\/g, '\\');
      }

      if (!labels.hostname || !labels.port) continue;

      const value = metricName.includes('timestamp') || metricName === 'ssl_cert_check_success' || metricName === 'ssl_cert_sans_count' || metricName === 'ssl_cert_serial' || metricName === 'ssl_cert_is_webtrust'
        ? parseInt(valueStr)
        : parseFloat(valueStr);

      const key = `${labels.hostname}:${labels.port}`;

      if (!metrics.has(key)) {
        metrics.set(key, {
          hostname: labels.hostname || '',
          port: labels.port || '443',
          owner: labels.owner || '未知',
          env: labels.env || '未知',
          service_name: labels.service_name || labels.hostname || '',
          subject_cn: labels.subject_cn || '',
          issuer_cn: labels.issuer_cn || '',
          issuer_org: labels.issuer_org || '',
          is_webtrust: 0,
        });
      } else {
        const existing = metrics.get(key);
        if (labels.subject_cn && !existing.subject_cn) existing.subject_cn = labels.subject_cn;
        if (labels.issuer_cn && !existing.issuer_cn) existing.issuer_cn = labels.issuer_cn;
        if (labels.issuer_org && !existing.issuer_org) existing.issuer_org = labels.issuer_org;
      }

      if (!values.has(key)) {
        values.set(key, {});
      }

      const metricKey = metricName.replace('ssl_cert_', '');
      values.get(key)[metricKey] = value;
    } catch (e) {
      continue;
    }
  }

  const result = [];
  for (const [key, metric] of metrics) {
    const vals = values.get(key) || {};
    const daysLeft = vals.days_left || 0;
    const checkSuccess = vals.check_success || 0;
    const notAfterTimestamp = vals.not_after_timestamp || 0;
    const notBeforeTimestamp = vals.not_before_timestamp || 0;
    const isWebtrust = vals.is_webtrust || 0;
    const sansCount = vals.sans_count || 0;

    let status = 'valid';
    if (checkSuccess === 0) {
      status = 'expired';
    } else if (daysLeft <= 7) {
      status = 'critical';
    } else if (daysLeft <= 30) {
      status = 'warning';
    }

    const notAfterDate = notAfterTimestamp ? new Date(notAfterTimestamp * 1000).toLocaleDateString('zh-CN') : '-';
    const notBeforeDate = notBeforeTimestamp ? new Date(notBeforeTimestamp * 1000).toLocaleDateString('zh-CN') : '-';

    result.push({
      ...metric,
      days_left: daysLeft,
      not_after_date: notAfterDate,
      not_before_date: notBeforeDate,
      check_success: checkSuccess,
      sans_count: sansCount,
      is_webtrust: isWebtrust,
      status,
    });
  }

  return result;
}

// ==================== 报告生成 ====================

function calculateStats(data) {
  const total = data.length;
  const valid = data.filter(d => d.status === 'valid').length;
  const warning = data.filter(d => d.status === 'warning').length;
  const critical = data.filter(d => d.status === 'critical').length;
  const expired = data.filter(d => d.status === 'expired').length;
  const averageDaysLeft = total > 0 ? Math.round(data.reduce((sum, d) => sum + d.days_left, 0) / total) : 0;

  return { total, valid, warning, critical, expired, average_days_left: averageDaysLeft };
}

function buildReportHTML(data, stats, credentialData = null) {
  const now = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });

  const abnormalCerts = data
    .filter(c => c.status !== 'valid')
    .sort((a, b) => a.days_left - b.days_left);

  const allCerts = [...data].sort((a, b) => a.days_left - b.days_left);

  const statusLabels = {
    valid: '正常',
    warning: '即将过期',
    critical: '紧急',
    expired: '已过期',
  };
  const statusColors = {
    valid: '#10b981',
    warning: '#f59e0b',
    critical: '#ef4444',
    expired: '#6b7280',
  };
  const statusBgColors = {
    valid: '#ecfdf5',
    warning: '#fffbeb',
    critical: '#fef2f2',
    expired: '#f9fafb',
  };

  // 异常证书行
  let abnormalRows = '';
  for (const cert of abnormalCerts) {
    const color = statusColors[cert.status];
    const bgColor = statusBgColors[cert.status];
    abnormalRows += `
      <tr style="background: ${bgColor};">
        <td style="padding: 10px; border: 1px solid #e5e7eb;">
          <span style="color: ${color}; font-weight: bold;">${statusLabels[cert.status]}</span>
        </td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.service_name}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.hostname}:${cert.port}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.owner}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.env}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb; color: ${color}; font-weight: bold;">${cert.days_left} 天</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.not_after_date}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.issuer_org || cert.issuer_cn || '-'}</td>
      </tr>`;
  }

  // 全部证书行
  let allRows = '';
  for (const cert of allCerts) {
    const color = statusColors[cert.status];
    const bgColor = statusBgColors[cert.status];
    allRows += `
      <tr style="background: ${bgColor};">
        <td style="padding: 10px; border: 1px solid #e5e7eb;">
          <span style="color: ${color}; font-weight: bold;">${statusLabels[cert.status]}</span>
        </td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.service_name}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.hostname}:${cert.port}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.owner}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.env}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb; color: ${color}; font-weight: bold;">${cert.days_left} 天</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.not_after_date}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cert.is_webtrust ? '是' : '否'}</td>
      </tr>`;
  }

  return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; background: #f0f2f5; }
    .container { max-width: 1000px; margin: 0 auto; padding: 20px; }
    .header { background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%); color: white; padding: 25px 30px; border-radius: 12px 12px 0 0; }
    .header h1 { margin: 0 0 8px 0; font-size: 24px; }
    .header p { margin: 0; opacity: 0.9; font-size: 14px; }
    .content { background: white; padding: 25px 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    .stats { display: flex; gap: 15px; margin-bottom: 25px; flex-wrap: wrap; }
    .stat-card { flex: 1; min-width: 120px; padding: 15px; border-radius: 8px; text-align: center; }
    .stat-number { font-size: 28px; font-weight: bold; }
    .stat-label { font-size: 12px; color: #666; margin-top: 5px; }
    .stat-valid { background: #ecfdf5; border: 1px solid #a7f3d0; }
    .stat-warning { background: #fffbeb; border: 1px solid #fde68a; }
    .stat-critical { background: #fef2f2; border: 1px solid #fecaca; }
    .stat-expired { background: #f9fafb; border: 1px solid #e5e7eb; }
    .stat-avg { background: #f0f9ff; border: 1px solid #bae6fd; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 25px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    th { background: #1f2937; color: white; padding: 12px; text-align: left; font-weight: 500; font-size: 13px; }
    td { padding: 10px; border: 1px solid #e5e7eb; font-size: 13px; }
    .section-title { margin: 25px 0 15px 0; color: #1f2937; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px; }
    .footer { margin-top: 25px; text-align: center; color: #9ca3af; font-size: 12px; padding-top: 20px; border-top: 1px solid #e5e7eb; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>📊 SSL 证书监控报告</h1>
      <p>生成时间: ${now}</p>
    </div>
    <div class="content">
      <div class="stats">
        <div class="stat-card stat-valid">
          <div class="stat-number" style="color: #10b981;">${stats.valid}</div>
          <div class="stat-label">正常</div>
        </div>
        <div class="stat-card stat-warning">
          <div class="stat-number" style="color: #f59e0b;">${stats.warning}</div>
          <div class="stat-label">即将过期</div>
        </div>
        <div class="stat-card stat-critical">
          <div class="stat-number" style="color: #ef4444;">${stats.critical + stats.expired}</div>
          <div class="stat-label">紧急/过期</div>
        </div>
        <div class="stat-card stat-avg">
          <div class="stat-number" style="color: #0ea5e9;">${stats.average_days_left} 天</div>
          <div class="stat-label">平均剩余</div>
        </div>
      </div>

      ${abnormalCerts.length > 0 ? `
      <h3 class="section-title">🚨 异常证书 (${abnormalCerts.length})</h3>
      <table>
        <thead>
          <tr>
            <th>状态</th><th>服务名称</th><th>主机</th><th>负责人</th><th>环境</th><th>剩余天数</th><th>到期日期</th><th>颁发者</th>
          </tr>
        </thead>
        <tbody>${abnormalRows}</tbody>
      </table>
      ` : ''}

      <h3 class="section-title">📋 全部证书列表 (${allCerts.length})</h3>
      <table>
        <thead>
          <tr>
            <th>状态</th><th>服务名称</th><th>主机</th><th>负责人</th><th>环境</th><th>剩余天数</th><th>到期日期</th><th>WebTrust</th>
          </tr>
        </thead>
        <tbody>${allRows}</tbody>
      </table>

      ${credentialData && credentialData.credentials.length > 0 ? buildCredentialSection(credentialData) : ''}
    </div>
    <div class="footer">
      <p>由 SSL Certificate Monitoring System 自动生成并发送</p>
      <p>如需修改报告发送配置，请在系统设置页面进行调整</p>
    </div>
  </div>
</body>
</html>`;
}

// ==================== 凭证报告 ====================

function buildCredentialSection(credentialData) {
  const { credentials, stats: credStats } = credentialData;
  if (!credentials || credentials.length === 0) return '';

  const typeLabels = { cert: 'SSL证书', key: '密钥', auth: '授权', password: '口令' };
  const statusLabels = { expired: '已过期', critical: '高危', warning: '预警', normal: '正常', unknown: '未知' };
  const statusColors = { expired: '#6b7280', critical: '#ef4444', warning: '#f59e0b', normal: '#10b981', unknown: '#9ca3af' };
  const statusBgColors = { expired: '#f9fafb', critical: '#fef2f2', warning: '#fffbeb', normal: '#ecfdf5', unknown: '#f9fafb' };

  // 异常凭证（非正常状态）
  const abnormalCreds = credentials
    .filter(c => c.status !== 'normal' && c.status !== 'unknown')
    .sort((a, b) => (a.days_left ?? 9999) - (b.days_left ?? 9999));

  // 全部凭证
  const allCreds = [...credentials].sort((a, b) => (a.days_left ?? 9999) - (b.days_left ?? 9999));

  // 异常凭证行
  let abnormalCredRows = '';
  for (const cred of abnormalCreds) {
    const color = statusColors[cred.status] || '#9ca3af';
    const bgColor = statusBgColors[cred.status] || '#f9fafb';
    const label = statusLabels[cred.status] || cred.status;
    const daysText = cred.days_left !== null && cred.days_left < 0 ? `已过期 ${Math.abs(cred.days_left)} 天` : `${cred.days_left} 天`;
    abnormalCredRows += `
      <tr style="background: ${bgColor};">
        <td style="padding: 10px; border: 1px solid #e5e7eb;">
          <span style="color: ${color}; font-weight: bold;">${label}</span>
        </td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.name}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${typeLabels[cred.type] || cred.type_label || cred.type}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.service_name || '-'}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.owner || '-'}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.env || '-'}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb; color: ${color}; font-weight: bold;">${daysText}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.expiry_date || '-'}</td>
      </tr>`;
  }

  // 全部凭证行
  let allCredRows = '';
  for (const cred of allCreds) {
    const color = statusColors[cred.status] || '#9ca3af';
    const bgColor = statusBgColors[cred.status] || '#f9fafb';
    const label = statusLabels[cred.status] || cred.status;
    const daysText = cred.days_left !== null && cred.days_left < 0 ? `已过期 ${Math.abs(cred.days_left)} 天` : (cred.days_left !== null ? `${cred.days_left} 天` : '-');
    allCredRows += `
      <tr style="background: ${bgColor};">
        <td style="padding: 10px; border: 1px solid #e5e7eb;">
          <span style="color: ${color}; font-weight: bold;">${label}</span>
        </td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.name}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${typeLabels[cred.type] || cred.type_label || cred.type}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.service_name || '-'}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.owner || '-'}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.env || '-'}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb; color: ${color}; font-weight: bold;">${daysText}</td>
        <td style="padding: 10px; border: 1px solid #e5e7eb;">${cred.expiry_date || '-'}</td>
      </tr>`;
  }

  return `
    <div style="margin-top: 30px; padding-top: 20px; border-top: 2px solid #e5e7eb;">
      <h3 class="section-title">🔑 凭证管理概览</h3>
      ${credStats ? `
      <div class="stats">
        <div class="stat-card" style="background: #eef2ff; border: 1px solid #c7d2fe;">
          <div class="stat-number" style="color: #6366f1;">${credStats.total}</div>
          <div class="stat-label">凭证总数</div>
        </div>
        <div class="stat-card stat-expired">
          <div class="stat-number" style="color: #6b7280;">${credStats.expired}</div>
          <div class="stat-label">已过期</div>
        </div>
        <div class="stat-card stat-critical">
          <div class="stat-number" style="color: #ef4444;">${credStats.critical}</div>
          <div class="stat-label">高危(&lt;7天)</div>
        </div>
        <div class="stat-card stat-warning">
          <div class="stat-number" style="color: #f59e0b;">${credStats.warning}</div>
          <div class="stat-label">预警(&lt;30天)</div>
        </div>
        <div class="stat-card stat-valid">
          <div class="stat-number" style="color: #10b981;">${credStats.normal}</div>
          <div class="stat-label">正常</div>
        </div>
      </div>
      ` : ''}

      ${abnormalCreds.length > 0 ? `
      <h3 class="section-title">🚨 异常凭证 (${abnormalCreds.length})</h3>
      <table>
        <thead>
          <tr>
            <th>状态</th><th>名称</th><th>类型</th><th>关联服务</th><th>负责人</th><th>环境</th><th>剩余天数</th><th>到期日期</th>
          </tr>
        </thead>
        <tbody>${abnormalCredRows}</tbody>
      </table>
      ` : ''}

      <h3 class="section-title">📋 全部凭证列表 (${allCreds.length})</h3>
      <table>
        <thead>
          <tr>
            <th>状态</th><th>名称</th><th>类型</th><th>关联服务</th><th>负责人</th><th>环境</th><th>剩余天数</th><th>到期日期</th>
          </tr>
        </thead>
        <tbody>${allCredRows}</tbody>
      </table>
    </div>`;
}

// ==================== 邮件发送 ====================

async function sendReportEmail(toEmails, htmlContent) {
  const smtpConfig = getSmtpConfig();
  if (!smtpConfig.host || !smtpConfig.user || !smtpConfig.password) {
    throw new Error('SMTP 配置不完整，请检查 SMTP_HOST, SMTP_USER, SMTP_PASSWORD 配置');
  }

  if (!toEmails || toEmails.length === 0) {
    throw new Error('收件人邮箱列表为空');
  }

  const transporter = nodemailer.createTransport({
    host: smtpConfig.host,
    port: smtpConfig.port,
    secure: smtpConfig.port === 465,
    auth: {
      user: smtpConfig.user,
      pass: smtpConfig.password,
    },
    tls: smtpConfig.useTLS ? { rejectUnauthorized: false } : undefined,
  });

  const now = new Date().toLocaleDateString('zh-CN');
  const mailOptions = {
    from: smtpConfig.from || smtpConfig.user,
    to: toEmails.join(','),
    subject: `📊 SSL证书监控报告 - ${now}`,
    html: htmlContent,
  };

  const info = await transporter.sendMail(mailOptions);
  console.log(`报告邮件已发送: ${info.response}`);
  return info;
}

function getSmtpConfig() {
  return {
    host: process.env.SMTP_HOST || '',
    port: parseInt(process.env.SMTP_PORT || '587'),
    user: process.env.SMTP_USER || '',
    password: process.env.SMTP_PASSWORD || '',
    from: process.env.SMTP_FROM || process.env.SMTP_USER || '',
    useTLS: (process.env.SMTP_USE_TLS || 'true').toLowerCase() === 'true',
  };
}

// ==================== 凭证数据获取 ====================

async function fetchCredentialData() {
  try {
    const response = await fetch(`${AGENT_SERVER_URL}/api/v1/credentials`, {
      signal: AbortSignal.timeout(15000),
    });
    if (response.ok) {
      const json = await response.json();
      if (json.status === 'success') {
        return {
          credentials: json.credentials || [],
          stats: json.stats || null,
        };
      }
    }
  } catch (error) {
    console.warn('[ReportScheduler] 获取凭证数据失败:', error.message);
  }
  return { credentials: [], stats: null };
}

// ==================== 数据获取 ====================

async function fetchAllMetrics() {
  const seenKeys = new Set();
  const allMetrics = [];

  // 1. 从 Agent Server 获取
  try {
    const response = await fetch(`${AGENT_SERVER_URL}/metrics`, {
      signal: AbortSignal.timeout(30000),
    });
    if (response.ok) {
      const text = await response.text();
      const data = parseMetrics(text);
      for (const item of data) {
        const key = `${item.hostname}:${item.port}`;
        if (!seenKeys.has(key)) {
          seenKeys.add(key);
          allMetrics.push(item);
        }
      }
      console.log(`[ReportScheduler] 从 Agent Server 获取 ${data.length} 条指标`);
    }
  } catch (error) {
    console.warn('[ReportScheduler] 从 Agent Server 获取失败:', error.message);
  }

  // 2. 从 Exporter 获取
  try {
    const response = await fetch(`${EXPORTER_URL}/metrics`, {
      signal: AbortSignal.timeout(120000),
    });
    if (response.ok) {
      const text = await response.text();
      const data = parseMetrics(text);
      for (const item of data) {
        const key = `${item.hostname}:${item.port}`;
        if (!seenKeys.has(key)) {
          seenKeys.add(key);
          allMetrics.push(item);
        }
      }
      console.log(`[ReportScheduler] 从 Exporter 获取 ${data.length} 条指标`);
    }
  } catch (error) {
    console.warn('[ReportScheduler] 从 Exporter 获取失败:', error.message);
  }

  return allMetrics;
}

// ==================== 定时任务 ====================

async function executeReportSend() {
  console.log('[ReportScheduler] 开始执行定时报告发送...');

  try {
    const scheduleConfig = getReportScheduleConfig();
    if (!scheduleConfig.admin_emails || scheduleConfig.admin_emails.length === 0) {
      console.warn('[ReportScheduler] 管理员邮箱为空，跳过发送');
      lastSendResult = {
        success: false,
        message: '管理员邮箱为空',
        timestamp: new Date().toISOString(),
      };
      return;
    }

    // 获取指标数据
    const data = await fetchAllMetrics();
    if (data.length === 0) {
      console.warn('[ReportScheduler] 无法获取指标数据，跳过发送');
      lastSendResult = {
        success: false,
        message: '无法获取指标数据',
        timestamp: new Date().toISOString(),
      };
      return;
    }

    // 获取凭证数据
    const credentialData = await fetchCredentialData();
    console.log(`[ReportScheduler] 获取到 ${credentialData.credentials.length} 条凭证数据`);

    // 生成报告
    const stats = calculateStats(data);
    const html = buildReportHTML(data, stats, credentialData);

    // 发送邮件
    await sendReportEmail(scheduleConfig.admin_emails, html);

    // 更新发送状态
    lastSendResult = {
      success: true,
      message: `成功发送至 ${scheduleConfig.admin_emails.join(', ')}`,
      timestamp: new Date().toISOString(),
      certs_total: data.length,
      certs_warning: stats.warning,
      certs_critical: stats.critical,
      certs_expired: stats.expired,
      credentials_total: credentialData.credentials.length,
      credentials_expired: credentialData.stats?.expired || 0,
      credentials_critical: credentialData.stats?.critical || 0,
      credentials_warning: credentialData.stats?.warning || 0,
      credentials_normal: credentialData.stats?.normal || 0,
    };

    // 保存到配置
    scheduleConfig.last_sent_at = new Date().toISOString();
    scheduleConfig.last_send_status = 'success';
    setReportScheduleConfig(scheduleConfig);

    console.log('[ReportScheduler] 定时报告发送成功');
  } catch (error) {
    console.error('[ReportScheduler] 定时报告发送失败:', error.message);
    lastSendResult = {
      success: false,
      message: error.message,
      timestamp: new Date().toISOString(),
    };

    // 更新状态
    const scheduleConfig = getReportScheduleConfig();
    scheduleConfig.last_sent_at = new Date().toISOString();
    scheduleConfig.last_send_status = 'failed: ' + error.message;
    setReportScheduleConfig(scheduleConfig);
  }
}

// 频率到 cron 表达式的映射
const FREQUENCY_CRON_MAP = {
  'hourly': '0 * * * *',        // 每小时
  'daily': '0 9 * * *',         // 每天 9:00
  'weekly': '0 9 * * 1',        // 每周一 9:00
  'monthly': '0 9 1 * *',       // 每月1日 9:00
  'biweekly': '0 9 1,15 * *',   // 每月1日和15日 9:00
};

export function startScheduler() {
  const scheduleConfig = getReportScheduleConfig();

  // 停止已有任务
  if (scheduledTask) {
    scheduledTask.stop();
    scheduledTask = null;
  }

  if (!scheduleConfig.enabled) {
    console.log('[ReportScheduler] 定时报告未启用');
    return;
  }

  const cronExpression = scheduleConfig.cron_expression || FREQUENCY_CRON_MAP[scheduleConfig.frequency] || FREQUENCY_CRON_MAP.daily;

  if (!cron.validate(cronExpression)) {
    console.error(`[ReportScheduler] 无效的 cron 表达式: ${cronExpression}`);
    return;
  }

  scheduledTask = cron.schedule(cronExpression, () => {
    executeReportSend();
  }, {
    timezone: 'Asia/Shanghai',
  });

  console.log(`[ReportScheduler] 定时报告已启动, cron: ${cronExpression}, 收件人: ${scheduleConfig.admin_emails?.join(', ') || '未配置'}`);
}

export function stopScheduler() {
  if (scheduledTask) {
    scheduledTask.stop();
    scheduledTask = null;
    console.log('[ReportScheduler] 定时报告已停止');
  }
}

export function restartScheduler() {
  stopScheduler();
  startScheduler();
}

// ==================== 导出函数供 API 使用 ====================

export {
  getReportScheduleConfig,
  setReportScheduleConfig,
  executeReportSend,
  getSmtpConfig,
  sendReportEmail,
  buildReportHTML,
  buildCredentialSection,
  fetchAllMetrics,
  fetchCredentialData,
  calculateStats,
  lastSendResult,
  FREQUENCY_CRON_MAP,
};

// 获取最新发送结果
export function getLastSendResult() {
  return lastSendResult;
}

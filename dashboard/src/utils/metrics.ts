import { SSLCertData, SSLCertMetric } from '../types';

// 使用相对路径，由Nginx反向代理
const METRICS_API_URL = '/api/metrics';
// Agent Server 代理路径
const AGENT_SERVER_METRICS_URL = '/api/agent/metrics';

// 解析Prometheus格式的metrics数据
export function parseMetrics(text: string): SSLCertData[] {
  try {
    const lines = text.split('\n');
    const metrics: Map<string, SSLCertMetric> = new Map();
    const values: Map<string, { [key: string]: number | string }> = new Map();
    
    for (const line of lines) {
      if (line.startsWith('#') || !line.trim()) continue;
      
      try {
        // 匹配格式: metric_name{label1="value1",...} value
        // 使用更宽松的匹配来处理包含 } 的标签值
        const match = line.match(/^(\w+)\{(.+)\}\s+([\d.eE+-]+)$/);
        if (!match) continue;
        
        const [, metricName, labelsStr, valueStr] = match;
        const labels: { [key: string]: string } = {};
        
        // 解析标签（处理转义的引号和嵌套的大括号）
        // 使用贪婪匹配来正确处理包含复杂值的标签
        const labelMatches = labelsStr.matchAll(/(\w+)="((?:[^"\\]|\\.)*)"/g);
        for (const [, key, value] of labelMatches) {
          // 解码转义的引号
          labels[key] = value.replace(/\\"/g, '"').replace(/\\\\/g, '\\');
        }
        
        // 跳过没有 hostname 和 port 的指标行
        if (!labels.hostname || !labels.port) continue;
        
        const value = metricName.includes('timestamp') || metricName === 'ssl_cert_check_success' || metricName === 'ssl_cert_sans_count' || metricName === 'ssl_cert_serial' || metricName === 'ssl_cert_is_webtrust'
          ? parseInt(valueStr)
          : parseFloat(valueStr);
        
        // 使用hostname+port作为唯一标识
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
            subject: labels.subject || '',
            issuer: labels.issuer || '',
            serial: labels.serial || '',
            is_webtrust: 0,
          });
        } else {
          // 如果已有记录，但新行包含证书详情标签，则更新这些字段
          const existing = metrics.get(key)!;
          if (labels.subject_cn && !existing.subject_cn) existing.subject_cn = labels.subject_cn;
          if (labels.issuer_cn && !existing.issuer_cn) existing.issuer_cn = labels.issuer_cn;
          if (labels.issuer_org && !existing.issuer_org) existing.issuer_org = labels.issuer_org;
          if (labels.subject && !existing.subject) existing.subject = labels.subject;
          if (labels.issuer && !existing.issuer) existing.issuer = labels.issuer;
          if (labels.serial && !existing.serial) existing.serial = labels.serial;
        }
        
        if (!values.has(key)) {
          values.set(key, {});
        }
        
        const metricKey = metricName.replace('ssl_cert_', '');
        values.get(key)![metricKey] = value;
      } catch (e) {
        // 跳过解析失败的行
        console.warn('解析 metrics 行失败:', line.substring(0, 100), e);
        continue;
      }
    }
    
    // 转换为完整的数据对象
    const result: SSLCertData[] = [];
    for (const [key, metric] of metrics) {
      const vals = values.get(key)!;
      
      const notAfterTimestamp = vals.not_after_timestamp as number || 0;
      const notBeforeTimestamp = vals.not_before_timestamp as number || 0;
      const daysLeft = vals.days_left as number || 0;
      const checkSuccess = vals.check_success as number || 0;
      const sansCount = vals.sans_count as number || 0;
      const isWebtrust = vals.is_webtrust as number || 0;
      
      // 计算状态
      let status: 'valid' | 'warning' | 'critical' | 'expired' | 'unreachable' = 'valid';
      let statusColor = '#10b981'; // green
      
      if (checkSuccess === 0) {
        status = 'unreachable';
        statusColor = '#6b7280'; // gray
      } else if (daysLeft <= 7) {
        status = 'critical';
        statusColor = '#ef4444'; // red
      } else if (daysLeft <= 30) {
        status = 'warning';
        statusColor = '#f59e0b'; // amber
      }
      
      result.push({
        ...metric,
        days_left: daysLeft,
        not_after_timestamp: notAfterTimestamp,
        not_before_timestamp: notBeforeTimestamp,
        check_success: checkSuccess,
        sans_count: sansCount,
        serial_value: vals.serial as number || 0,
        is_webtrust: isWebtrust,
        not_after_date: formatDate(notAfterTimestamp * 1000),
        not_before_date: formatDate(notBeforeTimestamp * 1000),
        days_until_expiry: daysLeft,
        status,
        status_color: statusColor,
      });
    }
    
    console.log(`parseMetrics 解析完成，共 ${result.length} 条记录`);
    return result;
  } catch (e) {
    console.error('parseMetrics 整体失败:', e);
    return [];
  }
}

function formatDate(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}

// 创建带超时的 fetch 请求
function fetchWithTimeout(url: string, timeoutMs: number): Promise<Response> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    
    fetch(url, { signal: controller.signal })
      .then(response => {
        clearTimeout(timeoutId);
        resolve(response);
      })
      .catch(error => {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
          reject(new Error(`请求超时 (${timeoutMs}ms)`));
        } else {
          reject(error);
        }
      });
  });
}

export async function fetchMetrics(): Promise<SSLCertData[]> {
  const seenKeys = new Set<string>(); // 用于去重
  
  console.log('fetchMetrics 开始请求...');
  
  // 并行请求两个数据源
  const [agentMetrics, exporterMetrics] = await Promise.all([
    // 1. 从 Agent Server 获取（Agent 监控的内网目标）- 较快返回
    fetchWithTimeout(AGENT_SERVER_METRICS_URL, 30000).then(async response => {
      console.log('Agent Server 响应状态:', response.status);
      if (!response.ok) throw new Error('Agent Server 请求失败');
      const text = await response.text();
      console.log('Agent Server 响应长度:', text.length);
      const data = parseMetrics(text);
      console.log(`从 Agent Server 获取 ${data.length} 条指标`);
      return data;
    }).catch(error => {
      console.warn('从 Agent Server 获取失败:', error);
      return [];
    }),
    
    // 2. 从 Custom Exporter 获取（Prometheus/blackbox-exporter 监控的目标）- 较慢
    fetchWithTimeout(METRICS_API_URL, 120000).then(async response => {
      console.log('Exporter 响应状态:', response.status);
      if (!response.ok) throw new Error('Exporter 请求失败');
      const text = await response.text();
      console.log('Exporter 响应长度:', text.length);
      const data = parseMetrics(text);
      console.log(`从 Exporter 获取 ${data.length} 条指标`);
      return data;
    }).catch(error => {
      // Exporter 超时只是警告，不影响显示
      console.warn('从 Exporter 获取失败（可能超时）:', error);
      return [];
    })
  ]);
  
  // 合并数据
  const allMetrics: SSLCertData[] = [];
  
  // 先添加 Agent Server 数据（通常更可靠）
  agentMetrics.forEach(item => {
    const key = `${item.hostname}:${item.port}`;
    if (!seenKeys.has(key)) {
      seenKeys.add(key);
      allMetrics.push(item);
    }
  });
  
  // 再添加 Exporter 数据（去重）
  exporterMetrics.forEach(item => {
    const key = `${item.hostname}:${item.port}`;
    if (!seenKeys.has(key)) {
      seenKeys.add(key);
      allMetrics.push(item);
    }
  });
  
  if (allMetrics.length === 0) {
    throw new Error('无法获取任何指标数据');
  }
  
  console.log(`合并后共 ${allMetrics.length} 条指标`);
  return allMetrics;
}

export function calculateStats(data: SSLCertData[]) {
  const total = data.length;
  const valid = data.filter(d => d.status === 'valid').length;
  const warning = data.filter(d => d.status === 'warning').length;
  const critical = data.filter(d => d.status === 'critical').length;
  const expired = data.filter(d => d.status === 'expired').length;
  const unreachable = data.filter(d => d.status === 'unreachable').length;
  
  const averageDaysLeft = total > 0
    ? Math.round(data.reduce((sum, d) => sum + d.days_left, 0) / total)
    : 0;
  
  return { total, valid, warning, critical, expired, unreachable, average_days_left: averageDaysLeft };
}

export function filterCertificates(data: SSLCertData[], filters: {
  owner?: string;
  env?: string;
  status?: string;
  search?: string;
}): SSLCertData[] {
  return data.filter(cert => {
    if (filters.owner && cert.owner !== filters.owner) return false;
    if (filters.env && cert.env !== filters.env) return false;
    if (filters.status && cert.status !== filters.status) return false;
    if (filters.search) {
      const searchLower = filters.search.toLowerCase();
      return (
        cert.service_name.toLowerCase().includes(searchLower) ||
        cert.hostname.toLowerCase().includes(searchLower) ||
        cert.owner.toLowerCase().includes(searchLower)
      );
    }
    return true;
  });
}

export function getUniqueValues(data: SSLCertData[], key: keyof SSLCertData): string[] {
  return [...new Set(data.map(d => String(d[key])))].sort();
}

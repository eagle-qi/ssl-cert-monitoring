export interface SSLCertMetric {
  hostname: string;
  port: string;
  owner: string;
  env: string;
  service_name: string;
  subject_cn?: string;
  issuer_cn?: string;
  issuer_org?: string;
  subject?: string;
  issuer?: string;
  serial?: string;
  is_webtrust?: number;
}

export interface SSLCertData extends SSLCertMetric {
  days_left: number;
  not_after_timestamp: number;
  not_before_timestamp: number;
  check_success: number;
  sans_count: number;
  serial_value?: number;
  
  // 计算字段
  not_after_date: string;
  not_before_date: string;
  days_until_expiry: number;
  status: 'valid' | 'warning' | 'critical' | 'expired' | 'unreachable';
  status_color: string;
}

export interface DashboardStats {
  total: number;
  valid: number;
  warning: number;
  critical: number;
  expired: number;
  unreachable: number;
  average_days_left: number;
}

export interface FilterOptions {
  owner: string;
  env: string;
  status: string;
  search: string;
}

export interface User {
  username: string;
  role: 'admin' | 'readonly';
  token: string;
  loginTime: number;
}

import { useState, useEffect } from 'react';
import { Settings as SettingsIcon, Mail, Clock, Send, TestTube, CheckCircle, XCircle, AlertTriangle, Plus, Trash2, RefreshCw } from 'lucide-react';

interface FrequencyOption {
  value: string;
  label: string;
  cron: string;
}

interface ReportScheduleConfig {
  enabled: boolean;
  frequency: string;
  cron_expression: string;
  admin_emails: string[];
  last_sent_at: string | null;
  last_send_status: string | null;
  smtp_configured: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_from: string | null;
  last_send_result: {
    success: boolean;
    message: string;
    timestamp: string;
    certs_total?: number;
    certs_warning?: number;
    certs_critical?: number;
    certs_expired?: number;
    credentials_total?: number;
    credentials_expired?: number;
    credentials_critical?: number;
    credentials_warning?: number;
    credentials_normal?: number;
  } | null;
  frequency_options: FrequencyOption[];
}

export default function Settings() {
  const [config, setConfig] = useState<ReportScheduleConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [newEmail, setNewEmail] = useState('');
  const [testing, setTesting] = useState(false);
  const [sending, setSending] = useState(false);
  const [testEmail, setTestEmail] = useState('');

  // 表单状态
  const [enabled, setEnabled] = useState(false);
  const [frequency, setFrequency] = useState('daily');
  const [adminEmails, setAdminEmails] = useState<string[]>([]);

  const loadConfig = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/report-schedule');
      const data = await response.json();
      if (data.success) {
        setConfig(data.data);
        setEnabled(data.data.enabled);
        setFrequency(data.data.frequency);
        setAdminEmails(data.data.admin_emails || []);
        setTestEmail(data.data.admin_emails?.[0] || '');
      }
    } catch (error) {
      console.error('加载配置失败:', error);
      setMessage({ type: 'error', text: '加载配置失败' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleSave = async () => {
    try {
      setSaving(true);
      setMessage(null);

      const response = await fetch('/api/report-schedule', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled,
          frequency,
          admin_emails: adminEmails,
        }),
      });

      const data = await response.json();
      if (data.success) {
        setMessage({ type: 'success', text: '配置保存成功' });
        loadConfig();
      } else {
        setMessage({ type: 'error', text: data.message || '保存失败' });
      }
    } catch (error) {
      setMessage({ type: 'error', text: '保存失败，请检查网络连接' });
    } finally {
      setSaving(false);
    }
  };

  const handleAddEmail = () => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!newEmail || !emailRegex.test(newEmail)) {
      setMessage({ type: 'error', text: '请输入有效的邮箱地址' });
      return;
    }
    if (adminEmails.includes(newEmail)) {
      setMessage({ type: 'error', text: '该邮箱已存在' });
      return;
    }
    setAdminEmails([...adminEmails, newEmail]);
    setNewEmail('');
  };

  const handleRemoveEmail = (email: string) => {
    setAdminEmails(adminEmails.filter(e => e !== email));
  };

  const handleTestEmail = async () => {
    if (!testEmail) {
      setMessage({ type: 'error', text: '请输入测试邮箱地址' });
      return;
    }

    try {
      setTesting(true);
      setMessage(null);

      const response = await fetch('/api/report-schedule/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to: testEmail }),
      });

      const data = await response.json();
      if (data.success) {
        setMessage({ type: 'success', text: `测试邮件已发送至 ${testEmail}，请检查收件箱` });
      } else {
        setMessage({ type: 'error', text: data.message || '测试邮件发送失败' });
      }
    } catch (error) {
      setMessage({ type: 'error', text: '测试邮件发送失败，请检查网络连接' });
    } finally {
      setTesting(false);
    }
  };

  const handleManualSend = async () => {
    try {
      setSending(true);
      setMessage(null);

      const response = await fetch('/api/report-schedule/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      const data = await response.json();
      if (data.success) {
        setMessage({ type: 'success', text: '报告发送已触发，请稍后查看发送结果' });
        // 3秒后刷新结果
        setTimeout(loadConfig, 3000);
      } else {
        setMessage({ type: 'error', text: data.message || '触发报告发送失败' });
      }
    } catch (error) {
      setMessage({ type: 'error', text: '触发报告发送失败' });
    } finally {
      setSending(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <RefreshCw className="h-12 w-12 text-primary-600 animate-spin" />
      </div>
    );
  }

  const smtpConfigured = config?.smtp_configured ?? false;
  const lastResult = config?.last_send_result;

  return (
    <div className="space-y-6 max-w-4xl">
      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">系统设置</h1>
        <p className="text-gray-500 mt-1">配置定时报告发送和邮件通知</p>
      </div>

      {/* 消息提示 */}
      {message && (
        <div className={`flex items-center space-x-2 p-4 rounded-lg ${
          message.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
        }`}>
          {message.type === 'success' ? (
            <CheckCircle className="h-5 w-5 flex-shrink-0" />
          ) : (
            <XCircle className="h-5 w-5 flex-shrink-0" />
          )}
          <span>{message.text}</span>
          <button onClick={() => setMessage(null)} className="ml-auto text-gray-400 hover:text-gray-600">&times;</button>
        </div>
      )}

      {/* SMTP 配置状态 */}
      <div className="card">
        <div className="flex items-center space-x-3 mb-4">
          <div className="p-2 rounded-lg bg-blue-50">
            <Mail className="h-5 w-5 text-blue-600" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-gray-900">SMTP 邮件配置</h3>
            <p className="text-sm text-gray-500">邮件发送服务器配置（通过环境变量设置）</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="p-3 bg-gray-50 rounded-lg">
            <p className="text-xs text-gray-500 mb-1">SMTP 服务器</p>
            <p className="text-sm font-medium text-gray-900">{config?.smtp_host || '未配置'}</p>
          </div>
          <div className="p-3 bg-gray-50 rounded-lg">
            <p className="text-xs text-gray-500 mb-1">端口</p>
            <p className="text-sm font-medium text-gray-900">{config?.smtp_port || '未配置'}</p>
          </div>
          <div className="p-3 bg-gray-50 rounded-lg">
            <p className="text-xs text-gray-500 mb-1">发件人</p>
            <p className="text-sm font-medium text-gray-900">{config?.smtp_from || '未配置'}</p>
          </div>
        </div>

        <div className="mt-4 flex items-center space-x-3">
          <span className={`inline-flex items-center space-x-1.5 px-3 py-1 rounded-full text-sm font-medium ${
            smtpConfigured ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
          }`}>
            {smtpConfigured ? <CheckCircle className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
            <span>{smtpConfigured ? 'SMTP 已配置' : 'SMTP 未配置'}</span>
          </span>
          {!smtpConfigured && (
            <p className="text-sm text-gray-500">
              请在 .env 文件中配置 SMTP_HOST, SMTP_USER, SMTP_PASSWORD 等环境变量后重启服务
            </p>
          )}
        </div>

        {/* 测试邮件 */}
        {smtpConfigured && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <p className="text-sm font-medium text-gray-700 mb-2">测试邮件发送</p>
            <div className="flex space-x-2">
              <input
                type="email"
                value={testEmail}
                onChange={(e) => setTestEmail(e.target.value)}
                placeholder="输入测试邮箱地址"
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
              <button
                onClick={handleTestEmail}
                disabled={testing || !testEmail}
                className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <TestTube className="h-4 w-4" />
                <span>{testing ? '发送中...' : '发送测试'}</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 定时报告配置 */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-3">
            <div className="p-2 rounded-lg bg-purple-50">
              <Clock className="h-5 w-5 text-purple-600" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">定时报告发送</h3>
              <p className="text-sm text-gray-500">定期生成 SSL 证书与凭证管理监控报告并发送至管理员邮箱</p>
            </div>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary-600"></div>
          </label>
        </div>

        {/* 发送频率 */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">发送频率</label>
          <select
            value={frequency}
            onChange={(e) => setFrequency(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          >
            {config?.frequency_options?.map(opt => (
              <option key={opt.value} value={opt.value}>
                {opt.label} ({opt.cron})
              </option>
            ))}
          </select>
          <p className="text-xs text-gray-500 mt-1">
            当前 cron 表达式: <code className="bg-gray-100 px-1.5 py-0.5 rounded">{config?.frequency_options?.find(o => o.value === frequency)?.cron || '0 9 * * *'}</code>
          </p>
        </div>

        {/* 管理员邮箱 */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">管理员邮箱</label>
          <div className="space-y-2">
            {adminEmails.map(email => (
              <div key={email} className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg">
                <div className="flex items-center space-x-2">
                  <Mail className="h-4 w-4 text-gray-400" />
                  <span className="text-sm text-gray-700">{email}</span>
                </div>
                <button
                  onClick={() => handleRemoveEmail(email)}
                  className="p-1 text-gray-400 hover:text-red-500 transition-colors"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
            <div className="flex space-x-2">
              <input
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddEmail()}
                placeholder="输入管理员邮箱地址"
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
              <button
                onClick={handleAddEmail}
                className="flex items-center space-x-1 px-3 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200"
              >
                <Plus className="h-4 w-4" />
                <span>添加</span>
              </button>
            </div>
          </div>
          {adminEmails.length === 0 && (
            <p className="text-xs text-amber-600 mt-1 flex items-center space-x-1">
              <AlertTriangle className="h-3 w-3" />
              <span>请至少添加一个管理员邮箱</span>
            </p>
          )}
        </div>

        {/* 保存按钮 */}
        <div className="flex items-center space-x-3 pt-4 border-t border-gray-200">
          <button
            onClick={handleSave}
            disabled={saving}
            className="btn-primary flex items-center space-x-2"
          >
            <SettingsIcon className="h-4 w-4" />
            <span>{saving ? '保存中...' : '保存配置'}</span>
          </button>
          <button
            onClick={handleManualSend}
            disabled={sending || adminEmails.length === 0 || !smtpConfigured}
            className="btn-secondary flex items-center space-x-2"
            title={!smtpConfigured ? 'SMTP 未配置' : adminEmails.length === 0 ? '请先添加管理员邮箱' : '立即发送报告'}
          >
            <Send className="h-4 w-4" />
            <span>{sending ? '发送中...' : '立即发送报告'}</span>
          </button>
        </div>
      </div>

      {/* 最近发送记录 */}
      <div className="card">
        <div className="flex items-center space-x-3 mb-4">
          <div className="p-2 rounded-lg bg-green-50">
            <Send className="h-5 w-5 text-green-600" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-gray-900">最近发送记录</h3>
            <p className="text-sm text-gray-500">定时报告最近一次的发送状态</p>
          </div>
        </div>

        {lastResult ? (
          <div className="space-y-3">
            <div className="flex items-center space-x-2">
              {lastResult.success ? (
                <CheckCircle className="h-5 w-5 text-green-500" />
              ) : (
                <XCircle className="h-5 w-5 text-red-500" />
              )}
              <span className={`text-sm font-medium ${lastResult.success ? 'text-green-700' : 'text-red-700'}`}>
                {lastResult.success ? '发送成功' : '发送失败'}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="p-3 bg-gray-50 rounded-lg">
                <p className="text-xs text-gray-500">发送时间</p>
                <p className="text-sm font-medium text-gray-900">
                  {lastResult.timestamp ? new Date(lastResult.timestamp).toLocaleString('zh-CN') : '-'}
                </p>
              </div>
              {lastResult.certs_total !== undefined && (
                <div className="p-3 bg-gray-50 rounded-lg">
                  <p className="text-xs text-gray-500">证书总数</p>
                  <p className="text-sm font-medium text-gray-900">{lastResult.certs_total}</p>
                </div>
              )}
              {lastResult.certs_warning !== undefined && (
                <div className="p-3 bg-amber-50 rounded-lg">
                  <p className="text-xs text-gray-500">证书预警</p>
                  <p className="text-sm font-medium text-amber-700">{lastResult.certs_warning}</p>
                </div>
              )}
              {lastResult.certs_critical !== undefined && (
                <div className="p-3 bg-red-50 rounded-lg">
                  <p className="text-xs text-gray-500">证书紧急/过期</p>
                  <p className="text-sm font-medium text-red-700">{(lastResult.certs_critical || 0) + (lastResult.certs_expired || 0)}</p>
                </div>
              )}
            </div>

            {/* 凭证统计 */}
            {lastResult.credentials_total !== undefined && (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <p className="text-xs font-medium text-indigo-600 mb-2">凭证管理</p>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                  <div className="p-3 bg-indigo-50 rounded-lg">
                    <p className="text-xs text-gray-500">凭证总数</p>
                    <p className="text-sm font-medium text-indigo-700">{lastResult.credentials_total}</p>
                  </div>
                  <div className="p-3 bg-gray-50 rounded-lg">
                    <p className="text-xs text-gray-500">已过期</p>
                    <p className="text-sm font-medium text-gray-700">{lastResult.credentials_expired || 0}</p>
                  </div>
                  <div className="p-3 bg-red-50 rounded-lg">
                    <p className="text-xs text-gray-500">高危(&lt;7天)</p>
                    <p className="text-sm font-medium text-red-700">{lastResult.credentials_critical || 0}</p>
                  </div>
                  <div className="p-3 bg-amber-50 rounded-lg">
                    <p className="text-xs text-gray-500">预警(&lt;30天)</p>
                    <p className="text-sm font-medium text-amber-700">{lastResult.credentials_warning || 0}</p>
                  </div>
                  <div className="p-3 bg-green-50 rounded-lg">
                    <p className="text-xs text-gray-500">正常</p>
                    <p className="text-sm font-medium text-green-700">{lastResult.credentials_normal || 0}</p>
                  </div>
                </div>
              </div>
            )}
            {!lastResult.success && (
              <div className="p-3 bg-red-50 rounded-lg">
                <p className="text-xs text-red-500 mb-1">错误信息</p>
                <p className="text-sm text-red-700">{lastResult.message}</p>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center py-8">
            <Send className="h-12 w-12 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500">暂无发送记录</p>
            <p className="text-sm text-gray-400">启用定时报告或手动发送后将显示发送状态</p>
          </div>
        )}

        {config?.last_sent_at && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <p className="text-sm text-gray-500">
              上次发送时间: {new Date(config.last_sent_at).toLocaleString('zh-CN')}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

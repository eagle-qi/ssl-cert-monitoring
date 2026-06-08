import { useState, useEffect, useCallback } from 'react';
import {
  Shield, Plus, Edit2, Trash2, Search, AlertTriangle, AlertOctagon,
  CheckCircle, Clock, Key, Award, Lock, RefreshCw,
  X, Save, Bell, Eye, EyeOff, Zap
} from 'lucide-react';

interface Credential {
  id: string;
  name: string;
  type: string;
  type_label: string;
  expiry_date: string;
  owner: string;
  owner_email: string;
  env: string;
  service_name: string;
  remark: string;
  enabled: boolean;
  notify_days: number;
  created_at: string;
  updated_at: string;
  status: string;
  days_left: number | null;
  level: string;
}

interface CredentialStats {
  total: number;
  expired: number;
  critical: number;
  warning: number;
  normal: number;
  unknown: number;
}

const CREDENTIAL_TYPES = [
  { value: 'cert', label: 'SSL证书', icon: Shield, color: 'blue' },
  { value: 'key', label: '密钥', icon: Key, color: 'purple' },
  { value: 'auth', label: '授权', icon: Award, color: 'green' },
  { value: 'password', label: '口令/密码', icon: Lock, color: 'orange' },
];

const ENV_OPTIONS = [
  { value: 'production', label: '生产环境' },
  { value: 'staging', label: '预发布环境' },
  { value: 'testing', label: '测试环境' },
  { value: 'development', label: '开发环境' },
];

const defaultFormData = {
  name: '',
  type: 'cert',
  expiry_date: '',
  owner: '',
  owner_email: '',
  env: 'production',
  service_name: '',
  remark: '',
  enabled: true,
  notify_days: 30,
};

export default function CredentialManager() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [stats, setStats] = useState<CredentialStats>({ total: 0, expired: 0, critical: 0, warning: 0, normal: 0, unknown: 0 });
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const [filterType, setFilterType] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editingCred, setEditingCred] = useState<Credential | null>(null);
  const [formData, setFormData] = useState(defaultFormData);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<{ count: number; alerts: any[] } | null>(null);

  const API_BASE = '/api/agent/api/v1/credentials';

  const fetchCredentials = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (keyword) params.set('keyword', keyword);
      if (filterType) params.set('type', filterType);
      if (filterStatus) params.set('status', filterStatus);
      const resp = await fetch(`${API_BASE}?${params.toString()}`);
      const data = await resp.json();
      if (data.status === 'success') {
        setCredentials(data.credentials || []);
        setStats(data.stats || { total: 0, expired: 0, critical: 0, warning: 0, normal: 0, unknown: 0 });
      }
    } catch (e) {
      console.error('获取凭证列表失败:', e);
    } finally {
      setLoading(false);
    }
  }, [keyword, filterType, filterStatus]);

  useEffect(() => {
    fetchCredentials();
  }, [fetchCredentials]);

  const handleOpenAdd = () => {
    setEditingCred(null);
    setFormData(defaultFormData);
    setFormError(null);
    setShowModal(true);
  };

  const handleOpenEdit = (cred: Credential) => {
    setEditingCred(cred);
    setFormData({
      name: cred.name,
      type: cred.type,
      expiry_date: cred.expiry_date,
      owner: cred.owner,
      owner_email: cred.owner_email,
      env: cred.env,
      service_name: cred.service_name,
      remark: cred.remark,
      enabled: cred.enabled,
      notify_days: cred.notify_days,
    });
    setFormError(null);
    setShowModal(true);
  };

  const handleSave = async () => {
    if (!formData.name.trim()) { setFormError('凭证名称不能为空'); return; }
    if (!formData.type) { setFormError('请选择凭证类型'); return; }
    if (!formData.expiry_date) { setFormError('请设置有效期截止日期'); return; }

    setSaving(true);
    setFormError(null);
    try {
      const url = editingCred ? `${API_BASE}/${editingCred.id}` : API_BASE;
      const method = editingCred ? 'PUT' : 'POST';
      const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });
      const data = await resp.json();
      if (data.status === 'success') {
        setShowModal(false);
        fetchCredentials();
      } else {
        setFormError(data.error || '操作失败');
      }
    } catch (e) {
      setFormError('网络错误，请重试');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定要删除此凭证吗？')) return;
    setDeletingId(id);
    try {
      const resp = await fetch(`${API_BASE}/${id}`, { method: 'DELETE' });
      const data = await resp.json();
      if (data.status === 'success') {
        fetchCredentials();
      }
    } catch (e) {
      console.error('删除失败:', e);
    } finally {
      setDeletingId(null);
    }
  };

  const handleToggleEnabled = async (cred: Credential) => {
    try {
      const resp = await fetch(`${API_BASE}/${cred.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !cred.enabled }),
      });
      const data = await resp.json();
      if (data.status === 'success') {
        fetchCredentials();
      }
    } catch (e) {
      console.error('切换状态失败:', e);
    }
  };

  const handleCheckNow = async () => {
    setChecking(true);
    setCheckResult(null);
    try {
      const resp = await fetch(`${API_BASE}/check`, { method: 'POST' });
      const data = await resp.json();
      if (data.status === 'success') {
        setCheckResult({ count: data.alerts_count, alerts: data.alerts });
      }
    } catch (e) {
      console.error('检查失败:', e);
    } finally {
      setChecking(false);
    }
  };

  const getStatusBadge = (status: string, daysLeft: number | null) => {
    switch (status) {
      case 'expired':
        return <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800"><AlertOctagon className="h-3 w-3 mr-1" />已过期 {Math.abs(daysLeft || 0)}天</span>;
      case 'critical':
        return <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-700"><AlertOctagon className="h-3 w-3 mr-1" />{daysLeft}天</span>;
      case 'warning':
        return <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-yellow-100 text-yellow-800"><AlertTriangle className="h-3 w-3 mr-1" />{daysLeft}天</span>;
      case 'normal':
        return <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-green-100 text-green-800"><CheckCircle className="h-3 w-3 mr-1" />{daysLeft}天</span>;
      default:
        return <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-gray-100 text-gray-600">未知</span>;
    }
  };

  const getTypeIcon = (type: string) => {
    const found = CREDENTIAL_TYPES.find(t => t.value === type);
    if (!found) return <Shield className="h-4 w-4" />;
    const Icon = found.icon;
    return <Icon className="h-4 w-4" />;
  };

  const getTypeBadge = (type: string, label: string) => {
    const colorMap: Record<string, string> = {
      cert: 'bg-blue-100 text-blue-800',
      key: 'bg-purple-100 text-purple-800',
      auth: 'bg-green-100 text-green-800',
      password: 'bg-orange-100 text-orange-800',
    };
    return (
      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorMap[type] || 'bg-gray-100 text-gray-800'}`}>
        {getTypeIcon(type)}
        <span className="ml-1">{label || type}</span>
      </span>
    );
  };

  const getEnvBadge = (env: string) => {
    const envMap: Record<string, string> = {
      production: 'bg-red-50 text-red-700',
      staging: 'bg-yellow-50 text-yellow-700',
      testing: 'bg-blue-50 text-blue-700',
      development: 'bg-gray-50 text-gray-700',
    };
    const labelMap: Record<string, string> = {
      production: '生产', staging: '预发布', testing: '测试', development: '开发',
    };
    return <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs ${envMap[env] || 'bg-gray-50 text-gray-700'}`}>{labelMap[env] || env}</span>;
  };

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center">
            <Key className="h-7 w-7 mr-2 text-sky-500" />
            凭证管理
          </h1>
          <p className="mt-1 text-sm text-gray-500">手工管理证书、密钥、授权、口令有效期，自动过期告警</p>
        </div>
        <div className="flex items-center space-x-3">
          <button
            onClick={handleCheckNow}
            disabled={checking}
            className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
          >
            {checking ? <RefreshCw className="h-4 w-4 mr-2 animate-spin" /> : <Zap className="h-4 w-4 mr-2" />}
            {checking ? '检查中...' : '立即检查'}
          </button>
          <button
            onClick={handleOpenAdd}
            className="inline-flex items-center px-4 py-2 bg-sky-600 text-white rounded-lg text-sm font-medium hover:bg-sky-700 shadow-sm"
          >
            <Plus className="h-4 w-4 mr-2" />
            添加凭证
          </button>
        </div>
      </div>

      {/* 检查结果提示 */}
      {checkResult && (
        <div className={`rounded-lg p-4 ${checkResult.count > 0 ? 'bg-red-50 border border-red-200' : 'bg-green-50 border border-green-200'}`}>
          <div className="flex items-center">
            {checkResult.count > 0 ? (
              <AlertTriangle className="h-5 w-5 text-red-500 mr-2" />
            ) : (
              <CheckCircle className="h-5 w-5 text-green-500 mr-2" />
            )}
            <span className={`text-sm font-medium ${checkResult.count > 0 ? 'text-red-800' : 'text-green-800'}`}>
              {checkResult.count > 0
                ? `发现 ${checkResult.count} 个即将过期或已过期的凭证，已触发告警通知`
                : '所有凭证有效期正常，无需告警'}
            </span>
            <button onClick={() => setCheckResult(null)} className="ml-auto text-gray-400 hover:text-gray-600">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        {[
          { label: '全部', value: stats.total, color: 'bg-sky-50 text-sky-700', icon: Key },
          { label: '已过期', value: stats.expired, color: 'bg-red-50 text-red-700', icon: AlertOctagon },
          { label: '高危', value: stats.critical, color: 'bg-red-50 text-red-600', icon: AlertOctagon },
          { label: '预警', value: stats.warning, color: 'bg-yellow-50 text-yellow-700', icon: AlertTriangle },
          { label: '正常', value: stats.normal, color: 'bg-green-50 text-green-700', icon: CheckCircle },
          { label: '未知', value: stats.unknown, color: 'bg-gray-50 text-gray-600', icon: Clock },
        ].map(item => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              onClick={() => setFilterStatus(filterStatus === item.label.toLowerCase() || (item.label === '全部' && filterStatus) ? '' : item.label === '全部' ? '' : item.label.toLowerCase())}
              className={`${item.color} rounded-xl p-4 border transition-all hover:shadow-md cursor-pointer`}
            >
              <div className="flex items-center justify-between">
                <Icon className="h-5 w-5 opacity-70" />
                {item.value > 0 && item.label !== '全部' && (
                  <span className="text-xs opacity-60">筛选</span>
                )}
              </div>
              <div className="mt-2 text-2xl font-bold">{item.value}</div>
              <div className="text-xs opacity-70">{item.label}</div>
            </button>
          );
        })}
      </div>

      {/* 筛选栏 */}
      <div className="flex flex-wrap items-center gap-3 bg-white rounded-lg p-4 border">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="搜索凭证名称、负责人、备注..."
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
          />
        </div>
        <select
          value={filterType}
          onChange={e => setFilterType(e.target.value)}
          className="px-3 py-2 border rounded-lg text-sm bg-white focus:ring-2 focus:ring-sky-500"
        >
          <option value="">全部类型</option>
          {CREDENTIAL_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
          className="px-3 py-2 border rounded-lg text-sm bg-white focus:ring-2 focus:ring-sky-500"
        >
          <option value="">全部状态</option>
          <option value="expired">已过期</option>
          <option value="critical">高危</option>
          <option value="warning">预警</option>
          <option value="normal">正常</option>
          <option value="unknown">未知</option>
        </select>
        <button
          onClick={() => { setKeyword(''); setFilterType(''); setFilterStatus(''); }}
          className="px-3 py-2 text-sm text-gray-600 hover:text-gray-900"
        >
          重置
        </button>
      </div>

      {/* 凭证列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <RefreshCw className="h-6 w-6 text-sky-500 animate-spin mr-2" />
          <span className="text-gray-500">加载中...</span>
        </div>
      ) : credentials.length === 0 ? (
        <div className="text-center py-20 bg-white rounded-lg border">
          <Key className="h-12 w-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 mb-4">暂无凭证记录</p>
          <button onClick={handleOpenAdd} className="inline-flex items-center px-4 py-2 bg-sky-600 text-white rounded-lg text-sm hover:bg-sky-700">
            <Plus className="h-4 w-4 mr-2" />添加第一个凭证
          </button>
        </div>
      ) : (
        <div className="bg-white rounded-lg border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b">
                  <th className="text-left px-4 py-3 font-medium text-gray-600">凭证名称</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">类型</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">有效期</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">状态</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">负责人</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">环境</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">启用</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {credentials.map(cred => (
                  <tr key={cred.id} className={`hover:bg-gray-50 ${!cred.enabled ? 'opacity-60' : ''} ${
                    cred.status === 'expired' || cred.status === 'critical' ? 'bg-red-50/30' : cred.status === 'warning' ? 'bg-yellow-50/30' : ''
                  }`}>
                    <td className="px-4 py-3">
                      <div>
                        <div className="font-medium text-gray-900">{cred.name}</div>
                        {cred.service_name && <div className="text-xs text-gray-500 mt-0.5">{cred.service_name}</div>}
                        {cred.remark && <div className="text-xs text-gray-400 mt-0.5 truncate max-w-[200px]" title={cred.remark}>{cred.remark}</div>}
                      </div>
                    </td>
                    <td className="px-4 py-3">{getTypeBadge(cred.type, cred.type_label)}</td>
                    <td className="px-4 py-3">
                      <div className="text-gray-900">{cred.expiry_date || '未设置'}</div>
                    </td>
                    <td className="px-4 py-3">{getStatusBadge(cred.status, cred.days_left)}</td>
                    <td className="px-4 py-3">
                      <div className="text-gray-900">{cred.owner || '-'}</div>
                      {cred.owner_email && <div className="text-xs text-gray-400">{cred.owner_email}</div>}
                    </td>
                    <td className="px-4 py-3">{getEnvBadge(cred.env)}</td>
                    <td className="px-4 py-3">
                      <button onClick={() => handleToggleEnabled(cred)} className="focus:outline-none" title={cred.enabled ? '点击禁用' : '点击启用'}>
                        {cred.enabled ? (
                          <Eye className="h-4 w-4 text-green-500" />
                        ) : (
                          <EyeOff className="h-4 w-4 text-gray-400" />
                        )}
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center space-x-2">
                        <button onClick={() => handleOpenEdit(cred)} className="text-sky-600 hover:text-sky-700" title="编辑">
                          <Edit2 className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => handleDelete(cred.id)}
                          disabled={deletingId === cred.id}
                          className="text-red-500 hover:text-red-700 disabled:opacity-50"
                          title="删除"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 添加/编辑 Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-6 py-4 border-b">
              <h2 className="text-lg font-semibold text-gray-900">
                {editingCred ? '编辑凭证' : '添加凭证'}
              </h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="px-6 py-4 space-y-4">
              {formError && (
                <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-2 text-sm text-red-700">{formError}</div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">凭证名称 <span className="text-red-500">*</span></label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={e => setFormData({ ...formData, name: e.target.value })}
                  placeholder="如：api.example.com SSL证书"
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">凭证类型 <span className="text-red-500">*</span></label>
                  <select
                    value={formData.type}
                    onChange={e => setFormData({ ...formData, type: e.target.value })}
                    className="w-full px-3 py-2 border rounded-lg text-sm bg-white focus:ring-2 focus:ring-sky-500"
                  >
                    {CREDENTIAL_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">有效期截止 <span className="text-red-500">*</span></label>
                  <input
                    type="date"
                    value={formData.expiry_date}
                    onChange={e => setFormData({ ...formData, expiry_date: e.target.value })}
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">负责人</label>
                  <input
                    type="text"
                    value={formData.owner}
                    onChange={e => setFormData({ ...formData, owner: e.target.value })}
                    placeholder="负责人姓名"
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">负责人邮箱</label>
                  <input
                    type="email"
                    value={formData.owner_email}
                    onChange={e => setFormData({ ...formData, owner_email: e.target.value })}
                    placeholder="告警通知邮箱"
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">关联服务</label>
                  <input
                    type="text"
                    value={formData.service_name}
                    onChange={e => setFormData({ ...formData, service_name: e.target.value })}
                    placeholder="关联的服务名称"
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">环境</label>
                  <select
                    value={formData.env}
                    onChange={e => setFormData({ ...formData, env: e.target.value })}
                    className="w-full px-3 py-2 border rounded-lg text-sm bg-white focus:ring-2 focus:ring-sky-500"
                  >
                    {ENV_OPTIONS.map(e => <option key={e.value} value={e.value}>{e.label}</option>)}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">提前告警天数</label>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={formData.notify_days}
                  onChange={e => setFormData({ ...formData, notify_days: parseInt(e.target.value) || 30 })}
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                />
                <p className="text-xs text-gray-400 mt-1">小于此天数时触发预警告警（默认30天），小于7天触发高危告警</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">备注</label>
                <textarea
                  value={formData.remark}
                  onChange={e => setFormData({ ...formData, remark: e.target.value })}
                  placeholder="备注信息..."
                  rows={2}
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500 resize-none"
                />
              </div>

              <div className="flex items-center space-x-3">
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.enabled}
                    onChange={e => setFormData({ ...formData, enabled: e.target.checked })}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-sky-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-sky-600"></div>
                </label>
                <span className="text-sm text-gray-700">启用监控</span>
              </div>
            </div>

            <div className="flex items-center justify-end space-x-3 px-6 py-4 border-t bg-gray-50">
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border rounded-lg hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="inline-flex items-center px-4 py-2 bg-sky-600 text-white rounded-lg text-sm font-medium hover:bg-sky-700 disabled:opacity-50"
              >
                <Save className="h-4 w-4 mr-2" />
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 告警规则说明 */}
      <div className="bg-sky-50 border border-sky-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-sky-900 flex items-center mb-2">
          <Bell className="h-4 w-4 mr-2" />
          告警规则
        </h3>
        <ul className="text-xs text-sky-800 space-y-1">
          <li>• 有效期剩余 &lt; 30天 → <span className="text-yellow-700 font-medium">预警告警</span>（黄色）</li>
          <li>• 有效期剩余 &lt; 7天 → <span className="text-red-700 font-medium">高危告警</span>（红色）</li>
          <li>• 有效期已过 → <span className="text-red-700 font-medium">高危告警</span>（红色）</li>
          <li>• 告警通过飞书 Webhook 和邮件通知发送</li>
          <li>• 定时检查间隔默认1小时，可点击"立即检查"手动触发</li>
        </ul>
      </div>
    </div>
  );
}

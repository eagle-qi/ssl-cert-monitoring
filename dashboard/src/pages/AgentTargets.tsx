import { useState, useEffect } from 'react';
import {
  RefreshCw,
  Search,
  CheckCircle,
  ExternalLink,
  Server,
  User,
  Globe,
  AlertCircle,
  Activity,
  Copy,
  Check,
  Wand2,
  X,
  Loader2,
  Plus,
  Pencil,
  Trash2,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';

interface AgentTarget {
  id: string;
  url: string;
  service_name: string;
  owner: string;
  owner_email?: string;
  env?: string;
  agent_id?: string;
  agent_host?: string;
  agent_name?: string;
  check_interval?: number;
  timeout?: number;
  enabled: boolean;
  created_at?: string;
}

interface Agent {
  agent_id: string;
  hostname: string;
  ip: string;
  status: string;
  last_heartbeat?: string;
  metrics_count?: number;
  host?: string;
  name?: string;
  push_mode?: boolean;
  agent_mode?: string;  // push / pull / dual
  port?: number;
  local_targets_count?: number;
}

interface TargetFormData {
  url: string;
  service_name: string;
  agent_id: string;
  owner: string;
  owner_email: string;
  env: string;
  check_interval: number;
  timeout: number;
  enabled: boolean;
}

const defaultFormData: TargetFormData = {
  url: '',
  service_name: '',
  agent_id: '',
  owner: '',
  owner_email: '',
  env: 'production',
  check_interval: 180,
  timeout: 30,
  enabled: true,
};

export default function AgentTargets() {
  const [targets, setTargets] = useState<AgentTarget[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [copiedAgentId, setCopiedAgentId] = useState<string | null>(null);
  const [stats, setStats] = useState<{
    agents: { total: number; online: number };
    targets: { total: number; enabled: number };
  }>({
    agents: { total: 0, online: 0 },
    targets: { total: 0, enabled: 0 }
  });

  // 自动发现相关状态
  const [showDiscoverModal, setShowDiscoverModal] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverResult, setDiscoverResult] = useState<{
    found: number;
    targets: AgentTarget[];
    results?: { agent_id: string; agent_name: string; status: string; added: number; updated: number; message?: string; source?: string }[];
    error?: string;
  } | null>(null);

  // 目标编辑相关状态
  const [showTargetModal, setShowTargetModal] = useState(false);
  const [editingTarget, setEditingTarget] = useState<AgentTarget | null>(null);
  const [formData, setFormData] = useState<TargetFormData>(defaultFormData);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // 添加 Agent 相关状态
  const [showAgentModal, setShowAgentModal] = useState(false);
  const [agentFormData, setAgentFormData] = useState({
    agent_id: '',
    host: '',
    port: 8091,
    name: '',
    agent_mode: 'pull' as 'push' | 'pull' | 'dual',
    use_https: false,
  });
  const [agentFormError, setAgentFormError] = useState<string | null>(null);
  const [savingAgent, setSavingAgent] = useState(false);

  // 删除确认
  const [deletingTargetId, setDeletingTargetId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Agent Server 地址 (8090 端口)
  const AGENT_SERVER_URL = '/api/agent';

  const fetchAgents = async () => {
    try {
      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agents`);
      const data = await resp.json();
      if (data.status === 'success') {
        setAgents(data.agents || []);
      }
    } catch (err) {
      console.error('Failed to fetch agents:', err);
    }
  };

  const fetchTargets = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`${AGENT_SERVER_URL}/api/v1/agent-targets`);
      const result = await response.json();
      
      if (result.status === 'success') {
        setTargets(result.targets || []);
      } else {
        setError(result.error || '获取目标列表失败');
      }
    } catch (err) {
      setError('获取目标列表失败，请检查网络连接');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchStats = async () => {
    try {
      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agents`);
      const data = await resp.json();
      const agentsList = data.agents || [];
      
      const targetsResp = await fetch(`${AGENT_SERVER_URL}/api/v1/agent-targets`);
      const targetsData = await targetsResp.json();
      const agentTargets = targetsData.targets || [];
      
      setStats({
        agents: { 
          total: agentsList.length, 
          online: agentsList.filter((a: Agent) => a.status === 'online').length 
        },
        targets: { 
          total: agentTargets.length, 
          enabled: agentTargets.filter((t: AgentTarget) => t.enabled).length 
        }
      });
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  };

  useEffect(() => {
    fetchAgents();
    fetchTargets();
    fetchStats();
    
    const interval = setInterval(() => {
      fetchAgents();
      fetchStats();
    }, 10000);
    
    return () => clearInterval(interval);
  }, []);

  const copyAgentId = (agentId: string) => {
    navigator.clipboard.writeText(agentId);
    setCopiedAgentId(agentId);
    setTimeout(() => setCopiedAgentId(null), 2000);
  };

  // 自动发现目标
  const handleDiscoverTargets = async (agentId: string) => {
    setDiscovering(true);
    setDiscoverResult(null);
    try {
      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agents/${encodeURIComponent(agentId)}/discover`, {
        method: 'POST',
      });
      const data = await resp.json();
      if (data.status === 'success') {
        setDiscoverResult({
          found: data.targets?.length || 0,
          targets: data.targets || [],
        });
      } else {
        setDiscoverResult({
          found: 0,
          targets: [],
          error: data.error || data.message || '自动发现失败',
        });
      }
    } catch (err) {
      setDiscoverResult({
        found: 0,
        targets: [],
        error: '网络错误，请检查 Agent 连接',
      });
    } finally {
      setDiscovering(false);
    }
  };

  // 发现所有 Agent 的目标
  const handleDiscoverAll = async () => {
    setDiscovering(true);
    setDiscoverResult(null);
    try {
      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agents/discover-all`, {
        method: 'POST',
      });
      const data = await resp.json();
      if (data.status === 'success') {
        setDiscoverResult({
          found: data.found || 0,
          targets: data.targets || [],
          results: data.results || [],
        });
      } else {
        setDiscoverResult({
          found: 0,
          targets: [],
          error: data.error || data.message || '自动发现失败',
        });
      }
    } catch (err) {
      setDiscoverResult({
        found: 0,
        targets: [],
        error: '网络错误，请稍后重试',
      });
    } finally {
      setDiscovering(false);
    }
  };

  // ====== Agent CRUD ======

  const handleOpenAddAgentModal = () => {
    setAgentFormData({
      agent_id: '',
      host: '',
      port: 8091,
      name: '',
      agent_mode: 'pull',
      use_https: false,
    });
    setAgentFormError(null);
    setShowAgentModal(true);
  };

  const handleSaveAgent = async () => {
    if (!agentFormData.agent_id.trim()) {
      setAgentFormError('请输入 Agent ID');
      return;
    }
    if (agentFormData.agent_mode !== 'push' && !agentFormData.host.trim()) {
      setAgentFormError('Pull/Dual 模式必须填写 Agent 主机地址');
      return;
    }

    setSavingAgent(true);
    setAgentFormError(null);

    try {
      const payload = {
        agent_id: agentFormData.agent_id.trim(),
        host: agentFormData.host.trim(),
        port: agentFormData.agent_mode === 'push' ? 0 : agentFormData.port,
        name: agentFormData.name.trim() || agentFormData.host.trim() || agentFormData.agent_id.trim(),
        agent_mode: agentFormData.agent_mode,
        use_https: agentFormData.use_https,
      };

      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await resp.json();
      if (data.status === 'success') {
        setShowAgentModal(false);
        fetchAgents();
        fetchStats();
      } else {
        setAgentFormError(data.error || data.message || '添加 Agent 失败');
      }
    } catch (err) {
      setAgentFormError('网络错误，请稍后重试');
    } finally {
      setSavingAgent(false);
    }
  };

  const handleDeleteAgent = async (agentId: string) => {
    if (!confirm('确定要删除此 Agent 吗？该 Agent 的所有监控目标也将被删除。')) return;
    try {
      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agents/${encodeURIComponent(agentId)}`, {
        method: 'DELETE',
      });
      const data = await resp.json();
      if (data.status === 'success') {
        fetchAgents();
        fetchTargets();
        fetchStats();
      } else {
        alert('删除失败: ' + (data.error || '未知错误'));
      }
    } catch (err) {
      alert('删除失败: 网络错误');
    }
  };

  // ====== 目标 CRUD ======

  const handleOpenAddModal = () => {
    setEditingTarget(null);
    setFormData({ ...defaultFormData, agent_id: agents.length > 0 ? agents[0].agent_id : '' });
    setFormError(null);
    setShowTargetModal(true);
  };

  const handleOpenEditModal = async (target: AgentTarget) => {
    setEditingTarget(target);
    setFormData({
      url: target.url,
      service_name: target.service_name || '',
      agent_id: target.agent_id || '',
      owner: target.owner || '',
      owner_email: target.owner_email || '',
      env: target.env || 'production',
      check_interval: target.check_interval || 180,
      timeout: target.timeout || 30,
      enabled: target.enabled !== false,
    });
    setFormError(null);
    setShowTargetModal(true);
  };

  const handleSaveTarget = async () => {
    if (!formData.url.trim()) {
      setFormError('请输入监控 URL');
      return;
    }
    if (!formData.agent_id) {
      setFormError('请选择 Agent');
      return;
    }

    setSaving(true);
    setFormError(null);

    try {
      const payload = {
        url: formData.url.trim(),
        service_name: formData.service_name.trim() || formData.url.trim(),
        agent_id: formData.agent_id,
        owner: formData.owner.trim(),
        owner_email: formData.owner_email.trim(),
        env: formData.env,
        check_interval: formData.check_interval,
        timeout: formData.timeout,
        enabled: formData.enabled,
      };

      let resp;
      if (editingTarget) {
        resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agent-targets/${encodeURIComponent(editingTarget.id)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } else {
        resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agent-targets`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      const data = await resp.json();
      if (data.status === 'success') {
        setShowTargetModal(false);
        fetchTargets();
        fetchStats();
      } else {
        setFormError(data.error || data.message || '保存失败');
      }
    } catch (err) {
      setFormError('网络错误，请稍后重试');
    } finally {
      setSaving(false);
    }
  };

  const handleToggleTarget = async (target: AgentTarget) => {
    try {
      const resp = await fetch(`${AGENT_SERVER_URL}/api/v1/agent-targets/${encodeURIComponent(target.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: !target.enabled,
          agent_id: target.agent_id,
        }),
      });
      const data = await resp.json();
      if (data.status === 'success') {
        fetchTargets();
        fetchStats();
      }
    } catch (err) {
      console.error('Toggle target failed:', err);
    }
  };

  const handleDeleteTarget = async () => {
    if (!deletingTargetId) return;
    setDeleting(true);
    try {
      // 查找目标以获取 agent_id
      const target = targets.find(t => t.id === deletingTargetId);
      const agentId = target?.agent_id || '';
      const resp = await fetch(
        `${AGENT_SERVER_URL}/api/v1/agent-targets/${encodeURIComponent(deletingTargetId)}${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`,
        { method: 'DELETE' }
      );
      const data = await resp.json();
      if (data.status === 'success') {
        setDeletingTargetId(null);
        fetchTargets();
        fetchStats();
      } else {
        alert('删除失败: ' + (data.error || '未知错误'));
      }
    } catch (err) {
      alert('删除失败: 网络错误');
    } finally {
      setDeleting(false);
    }
  };

  const filteredTargets = targets.filter(target =>
    target.url.toLowerCase().includes(searchTerm.toLowerCase()) ||
    target.service_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    target.owner.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getAgentName = (agentId?: string) => {
    if (!agentId) return '未分配';
    let agent = agents.find(a => a.agent_id === agentId);
    if (!agent) {
      agent = agents.find(a => a.host === agentId || a.ip === agentId);
    }
    return agent ? (agent.hostname || agent.ip || agent.name || 'Agent') : agentId.substring(0, 8);
  };

  const getAgentStatus = (agentId?: string) => {
    if (!agentId) return null;
    let agent = agents.find(a => a.agent_id === agentId);
    if (!agent) {
      agent = agents.find(a => a.host === agentId || a.ip === agentId);
    }
    if (!agent) return 'offline';
    return agent.status;
  };

  if (loading && targets.length === 0) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <RefreshCw className="h-12 w-12 text-primary-600 animate-spin mx-auto mb-4" />
          <p className="text-gray-500">加载中...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Agent 目标管理</h1>
          <p className="text-gray-500 mt-1">
            管理分配给 Agent 的监控目标（支持 push/pull/dual 三种模式）
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <button
            onClick={handleOpenAddAgentModal}
            className="btn-secondary flex items-center space-x-2"
            title="手动添加 Agent（适用于 Pull 模式）"
          >
            <Server className="h-4 w-4" />
            <span>添加 Agent</span>
          </button>
          <button
            onClick={handleOpenAddModal}
            className="btn-primary flex items-center space-x-2"
            title="添加监控目标"
          >
            <Plus className="h-4 w-4" />
            <span>添加目标</span>
          </button>
          <button
            onClick={() => {
              setShowDiscoverModal(true);
              setDiscoverResult(null);
            }}
            className="btn-secondary flex items-center space-x-2"
            title="自动发现目标"
          >
            <Wand2 className="h-4 w-4" />
            <span>自动发现</span>
          </button>
          <button
            onClick={() => fetchTargets()}
            className="btn-secondary flex items-center space-x-2"
            title="刷新目标列表"
          >
            <RefreshCw className="h-4 w-4" />
            <span>刷新</span>
          </button>
        </div>
      </div>

      {/* Error Message */}
      {error && (
        <div className="bg-red-50 text-red-800 p-4 rounded-lg border border-red-200 flex items-center">
          <AlertCircle className="h-5 w-5 mr-2" />
          {error}
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="card">
          <div className="flex items-center space-x-4">
            <div className="p-3 rounded-lg bg-blue-50">
              <Server className="h-6 w-6 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Agent 总数</p>
              <p className="text-2xl font-bold text-gray-900">{stats.agents.total}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center space-x-4">
            <div className="p-3 rounded-lg bg-green-50">
              <Activity className="h-6 w-6 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">在线 Agent</p>
              <p className="text-2xl font-bold text-green-600">{stats.agents.online}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center space-x-4">
            <div className="p-3 rounded-lg bg-purple-50">
              <Globe className="h-6 w-6 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">监控目标</p>
              <p className="text-2xl font-bold text-gray-900">{stats.targets.total}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center space-x-4">
            <div className="p-3 rounded-lg bg-green-50">
              <CheckCircle className="h-6 w-6 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">已启用</p>
              <p className="text-2xl font-bold text-gray-900">{stats.targets.enabled}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Agent List */}
      <div className="card">
        <div className="card-header flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">在线 Agent</h2>
        </div>
        {agents.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            <Server className="h-12 w-12 mx-auto mb-3 text-gray-300" />
            <p>暂无 Agent 注册</p>
            <p className="text-sm mt-1">部署 Agent 后将自动显示在这里</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full">
              <thead>
                <tr>
                  <th className="text-left">主机名</th>
                  <th className="text-left">IP 地址</th>
                  <th className="text-left">模式</th>
                  <th className="text-left">状态</th>
                  <th className="text-left">Agent ID</th>
                  <th className="text-left">最后心跳</th>
                  <th className="text-left">操作</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((agent) => (
                  <tr key={agent.agent_id} className="border-t">
                    <td className="py-3">
                      <div className="flex items-center">
                        <Server className="h-5 w-5 text-gray-400 mr-2" />
                        <span className="font-medium">{agent.hostname || 'Unknown'}</span>
                      </div>
                    </td>
                    <td className="text-gray-600">{agent.ip || '-'}</td>
                    <td>
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        agent.agent_mode === 'dual'
                          ? 'bg-purple-100 text-purple-700'
                          : agent.agent_mode === 'push' || agent.push_mode
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-100 text-gray-600'
                      }`}>
                        {agent.agent_mode === 'dual' ? '双模式' : (agent.agent_mode === 'push' || agent.push_mode) ? '推送' : '拉取'}
                      </span>
                    </td>
                    <td>
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        agent.status === 'online' 
                          ? 'bg-green-100 text-green-700' 
                          : 'bg-yellow-100 text-yellow-700'
                      }`}>
                        {agent.status === 'online' ? '在线' : '离线'}
                      </span>
                    </td>
                    <td>
                      <div className="flex items-center space-x-2">
                        <code className="text-xs bg-gray-100 px-2 py-1 rounded">
                          {agent.agent_id.substring(0, 8)}...
                        </code>
                        <button
                          onClick={() => copyAgentId(agent.agent_id)}
                          className="p-1 hover:bg-gray-100 rounded"
                          title="复制完整ID"
                        >
                          {copiedAgentId === agent.agent_id ? (
                            <Check className="h-4 w-4 text-green-600" />
                          ) : (
                            <Copy className="h-4 w-4 text-gray-400" />
                          )}
                        </button>
                      </div>
                    </td>
                    <td className="text-gray-600 text-sm">
                      {agent.last_heartbeat 
                        ? new Date(agent.last_heartbeat).toLocaleString('zh-CN')
                        : '-'}
                    </td>
                    <td>
                      <div className="flex items-center space-x-2">
                        <button
                          onClick={() => {
                            setFormData({ ...defaultFormData, agent_id: agent.agent_id });
                            setEditingTarget(null);
                            setFormError(null);
                            setShowTargetModal(true);
                          }}
                          className="text-primary-600 hover:text-primary-700 text-sm flex items-center space-x-1"
                          title="为此 Agent 添加目标"
                        >
                          <Plus className="h-4 w-4" />
                          <span>添加目标</span>
                        </button>
                        <span className="text-gray-300">|</span>
                        <button
                          onClick={() => handleDiscoverTargets(agent.agent_id)}
                          className="text-primary-600 hover:text-primary-700 text-sm flex items-center space-x-1"
                          title={
                            agent.agent_mode === 'push' || (agent.push_mode && agent.agent_mode !== 'dual')
                              ? '从 Agent 心跳数据中发现目标'
                              : '自动发现该 Agent 上的目标'
                          }
                        >
                          <Wand2 className="h-4 w-4 mr-1" />
                          发现
                        </button>
                        {(agent.agent_mode === 'push' || (agent.push_mode && agent.agent_mode !== 'dual')) && (
                          <span className="text-xs text-gray-400" title="推送模式从心跳中获取 Agent 本地目标列表">
                            (心跳)
                          </span>
                        )}
                        <span className="text-gray-300">|</span>
                        <button
                          onClick={() => handleDeleteAgent(agent.agent_id)}
                          className="text-gray-400 hover:text-red-600 text-sm"
                          title="删除 Agent"
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
        )}
      </div>

      {/* Search */}
      <div className="card">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-gray-400" />
          <input
            type="text"
            placeholder="搜索 URL、服务名、负责人..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          />
        </div>
      </div>

      {/* Targets List */}
      <div className="space-y-4">
        {filteredTargets.length === 0 ? (
          <div className="card text-center py-12">
            <Globe className="h-16 w-16 text-gray-400 mx-auto mb-4" />
            <h2 className="text-xl font-semibold text-gray-900 mb-2">
              {searchTerm ? '没有找到匹配的目标' : '暂无监控目标'}
            </h2>
            <p className="text-gray-500 mb-6">
              {searchTerm ? '请尝试调整搜索条件' : '点击上方"添加目标"按钮为 Agent 配置监控 URL'}
            </p>
            {!searchTerm && (
              <button
                onClick={handleOpenAddModal}
                className="btn-primary inline-flex items-center space-x-2"
              >
                <Plus className="h-4 w-4" />
                <span>添加目标</span>
              </button>
            )}
          </div>
        ) : (
          filteredTargets.map((target) => (
            <div 
              key={target.id} 
              className={`card transition-all ${
                target.enabled 
                  ? 'bg-white border-gray-200' 
                  : 'bg-gray-50 border-gray-200 opacity-60'
              }`}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-start space-x-4 flex-1">
                  <div className={`p-2 rounded-lg ${
                    target.enabled ? 'bg-primary-50' : 'bg-gray-100'
                  }`}>
                    <Globe className={`h-6 w-6 ${
                      target.enabled ? 'text-primary-600' : 'text-gray-400'
                    }`} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center space-x-2 mb-2">
                      <h3 className="text-lg font-semibold text-gray-900">
                        {target.service_name}
                      </h3>
                      {!target.enabled && (
                        <span className="px-2 py-1 rounded text-xs font-medium bg-gray-200 text-gray-600">
                          已禁用
                        </span>
                      )}
                      {target.env && (
                        <span className={`px-2 py-1 rounded text-xs font-medium ${
                          target.env === 'production' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
                        }`}>
                          {target.env}
                        </span>
                      )}
                    </div>
                    
                    <a 
                      href={target.url} 
                      target="_blank" 
                      rel="noopener noreferrer"
                      className="text-primary-600 hover:text-primary-700 text-sm flex items-center space-x-1 mb-3"
                    >
                      <span>{target.url}</span>
                      <ExternalLink className="h-3 w-3" />
                    </a>

                    <div className="flex flex-wrap gap-4 text-sm text-gray-600">
                      {target.owner && (
                        <div className="flex items-center space-x-1">
                          <User className="h-4 w-4" />
                          <span>{target.owner}</span>
                        </div>
                      )}
                      {target.timeout && (
                        <div className="flex items-center space-x-1">
                          <span>超时 {target.timeout}s</span>
                        </div>
                      )}
                      {target.check_interval && (
                        <div className="flex items-center space-x-1">
                          <span>间隔 {target.check_interval}s</span>
                        </div>
                      )}
                      <div className="flex items-center space-x-2">
                        <Server className="h-4 w-4" />
                        <span>分配到: </span>
                        <span className={`font-medium ${
                          getAgentStatus(target.agent_id) === 'online' 
                            ? 'text-green-600' 
                            : 'text-gray-500'
                        }`}>
                          {target.agent_name || getAgentName(target.agent_id)}
                        </span>
                        {target.agent_id && (
                          <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">
                            {target.agent_id.substring(0, 8)}
                          </code>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {/* 操作按钮 */}
                <div className="flex items-center space-x-1 ml-4">
                  <button
                    onClick={() => handleToggleTarget(target)}
                    className={`p-2 rounded-lg hover:bg-gray-100 transition-colors ${
                      target.enabled ? 'text-green-600' : 'text-gray-400'
                    }`}
                    title={target.enabled ? '点击禁用' : '点击启用'}
                  >
                    {target.enabled ? (
                      <ToggleRight className="h-5 w-5" />
                    ) : (
                      <ToggleLeft className="h-5 w-5" />
                    )}
                  </button>
                  <button
                    onClick={() => handleOpenEditModal(target)}
                    className="p-2 rounded-lg hover:bg-gray-100 text-gray-500 hover:text-primary-600 transition-colors"
                    title="编辑"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setDeletingTargetId(target.id)}
                    className="p-2 rounded-lg hover:bg-red-50 text-gray-500 hover:text-red-600 transition-colors"
                    title="删除"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* ====== 添加/编辑目标 Modal ====== */}
      {showTargetModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-6 py-4 border-b sticky top-0 bg-white z-10">
              <h3 className="text-lg font-semibold">
                {editingTarget ? '编辑监控目标' : '添加监控目标'}
              </h3>
              <button
                onClick={() => setShowTargetModal(false)}
                className="p-1 hover:bg-gray-100 rounded"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              {formError && (
                <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm flex items-center">
                  <AlertCircle className="h-4 w-4 mr-2 flex-shrink-0" />
                  {formError}
                </div>
              )}

              {/* Agent 选择 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Agent <span className="text-red-500">*</span>
                </label>
                <select
                  value={formData.agent_id}
                  onChange={(e) => setFormData({ ...formData, agent_id: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                >
                  <option value="">-- 请选择 Agent --</option>
                  {agents.map((agent) => {
                    const modeLabel = agent.agent_mode === 'dual' ? '双模式' : (agent.agent_mode === 'push' || agent.push_mode) ? '推送' : '拉取';
                    return (
                      <option key={agent.agent_id} value={agent.agent_id}>
                        {agent.hostname || agent.name || agent.agent_id} ({modeLabel})
                      </option>
                    );
                  })}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  选择此目标分配给哪个 Agent 监控。推送模式 Agent 也能正常接收目标配置。
                </p>
              </div>

              {/* URL */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  监控 URL <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={formData.url}
                  onChange={(e) => setFormData({ ...formData, url: e.target.value })}
                  placeholder="例如: https://example.com:443 或 192.168.1.100:8443"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
                <p className="text-xs text-gray-500 mt-1">
                  支持 HTTPS URL 或 IP:端口 格式
                </p>
              </div>

              {/* 服务名称 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  服务名称
                </label>
                <input
                  type="text"
                  value={formData.service_name}
                  onChange={(e) => setFormData({ ...formData, service_name: e.target.value })}
                  placeholder="例如: 生产环境Web服务"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </div>

              {/* 负责人 + 邮箱 */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    负责人
                  </label>
                  <input
                    type="text"
                    value={formData.owner}
                    onChange={(e) => setFormData({ ...formData, owner: e.target.value })}
                    placeholder="姓名"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    负责人邮箱
                  </label>
                  <input
                    type="email"
                    value={formData.owner_email}
                    onChange={(e) => setFormData({ ...formData, owner_email: e.target.value })}
                    placeholder="email@example.com"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>
              </div>

              {/* 环境 + 检测间隔 + 超时 */}
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    环境
                  </label>
                  <select
                    value={formData.env}
                    onChange={(e) => setFormData({ ...formData, env: e.target.value })}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  >
                    <option value="production">生产环境</option>
                    <option value="staging">预发布</option>
                    <option value="testing">测试环境</option>
                    <option value="development">开发环境</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    检测间隔 (秒)
                  </label>
                  <input
                    type="number"
                    value={formData.check_interval}
                    onChange={(e) => setFormData({ ...formData, check_interval: parseInt(e.target.value) || 180 })}
                    min={30}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    超时 (秒)
                  </label>
                  <input
                    type="number"
                    value={formData.timeout}
                    onChange={(e) => setFormData({ ...formData, timeout: parseInt(e.target.value) || 30 })}
                    min={5}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>
              </div>

              {/* 启用开关 */}
              <div className="flex items-center space-x-3">
                <button
                  type="button"
                  onClick={() => setFormData({ ...formData, enabled: !formData.enabled })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    formData.enabled ? 'bg-primary-600' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      formData.enabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
                <span className="text-sm text-gray-700">
                  {formData.enabled ? '已启用' : '已禁用'}
                </span>
              </div>
            </div>

            <div className="flex items-center justify-end space-x-3 px-6 py-4 border-t bg-gray-50 sticky bottom-0">
              <button
                onClick={() => setShowTargetModal(false)}
                className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={handleSaveTarget}
                disabled={saving}
                className="btn-primary flex items-center space-x-2 disabled:opacity-50"
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                <span>{saving ? '保存中...' : '保存'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====== 添加 Agent Modal ====== */}
      {showAgentModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-6 py-4 border-b sticky top-0 bg-white z-10">
              <h3 className="text-lg font-semibold">添加 Agent</h3>
              <button
                onClick={() => setShowAgentModal(false)}
                className="p-1 hover:bg-gray-100 rounded"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              {agentFormError && (
                <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm flex items-center">
                  <AlertCircle className="h-4 w-4 mr-2 flex-shrink-0" />
                  {agentFormError}
                </div>
              )}

              {/* Agent ID */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Agent ID <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={agentFormData.agent_id}
                  onChange={(e) => setAgentFormData({ ...agentFormData, agent_id: e.target.value })}
                  placeholder="例如: pull-agent-01（唯一标识符）"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Agent 的唯一标识，需与 Agent 端配置的 AGENT_ID 一致
                </p>
              </div>

              {/* Agent 模式 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Agent 模式 <span className="text-red-500">*</span>
                </label>
                <div className="grid grid-cols-3 gap-3">
                  {(['pull', 'push', 'dual'] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setAgentFormData({ ...agentFormData, agent_mode: mode })}
                      className={`px-3 py-2 rounded-lg border-2 text-sm font-medium transition-colors ${
                        agentFormData.agent_mode === mode
                          ? mode === 'dual'
                            ? 'border-purple-500 bg-purple-50 text-purple-700'
                            : mode === 'push'
                              ? 'border-blue-500 bg-blue-50 text-blue-700'
                              : 'border-gray-500 bg-gray-50 text-gray-700'
                          : 'border-gray-200 text-gray-500 hover:border-gray-300'
                      }`}
                    >
                      {mode === 'pull' ? '拉取 (Pull)' : mode === 'push' ? '推送 (Push)' : '双模式 (Dual)'}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  {agentFormData.agent_mode === 'pull' && 'Server 主动连接 Agent 拉取数据，需填写主机地址'}
                  {agentFormData.agent_mode === 'push' && 'Agent 主动推送数据到 Server，主机地址可选'}
                  {agentFormData.agent_mode === 'dual' && '同时支持推拉两种方式，需填写主机地址'}
                </p>
              </div>

              {/* 主机地址 + 端口 */}
              <div className="grid grid-cols-3 gap-4">
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    主机地址 {agentFormData.agent_mode !== 'push' && <span className="text-red-500">*</span>}
                  </label>
                  <input
                    type="text"
                    value={agentFormData.host}
                    onChange={(e) => setAgentFormData({ ...agentFormData, host: e.target.value })}
                    placeholder="例如: 192.168.1.100 或 agent.example.com"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    端口
                  </label>
                  <input
                    type="number"
                    value={agentFormData.port}
                    onChange={(e) => setAgentFormData({ ...agentFormData, port: parseInt(e.target.value) || 8091 })}
                    min={1}
                    max={65535}
                    disabled={agentFormData.agent_mode === 'push'}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 disabled:bg-gray-100 disabled:text-gray-400"
                  />
                </div>
              </div>

              {/* 名称 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  名称
                </label>
                <input
                  type="text"
                  value={agentFormData.name}
                  onChange={(e) => setAgentFormData({ ...agentFormData, name: e.target.value })}
                  placeholder="例如: 生产环境 Agent（留空则使用主机地址）"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </div>

              {/* HTTPS */}
              <div className="flex items-center space-x-3">
                <button
                  type="button"
                  onClick={() => setAgentFormData({ ...agentFormData, use_https: !agentFormData.use_https })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    agentFormData.use_https ? 'bg-primary-600' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      agentFormData.use_https ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
                <span className="text-sm text-gray-700">
                  使用 HTTPS 连接 Agent
                </span>
              </div>

              {/* 使用说明 */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <h4 className="text-sm font-medium text-blue-800 mb-2">使用说明</h4>
                <ul className="text-xs text-blue-700 space-y-1">
                  <li>• <strong>Pull 模式</strong>：Agent 暴露 HTTP 端口（默认 8091），Server 主动拉取数据。需确保 Server 可访问 Agent 的 IP:Port</li>
                  <li>• <strong>Push 模式</strong>：Agent 主动推送心跳和指标到 Server（首次心跳时自动注册，也可在此手动添加）</li>
                  <li>• <strong>Dual 模式</strong>：同时支持 Push 和 Pull，提供数据冗余</li>
                  <li>• Agent ID 需与 Agent 端 .env 中配置的 <code className="bg-blue-100 px-1 rounded">AGENT_ID</code> 一致</li>
                </ul>
              </div>
            </div>

            <div className="flex items-center justify-end space-x-3 px-6 py-4 border-t bg-gray-50 sticky bottom-0">
              <button
                onClick={() => setShowAgentModal(false)}
                className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={handleSaveAgent}
                disabled={savingAgent}
                className="btn-primary flex items-center space-x-2 disabled:opacity-50"
              >
                {savingAgent && <Loader2 className="h-4 w-4 animate-spin" />}
                <span>{savingAgent ? '添加中...' : '添加 Agent'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====== 删除确认 Modal ====== */}
      {deletingTargetId && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md">
            <div className="p-6">
              <div className="flex items-center space-x-3 mb-4">
                <div className="p-2 rounded-lg bg-red-50">
                  <AlertCircle className="h-6 w-6 text-red-600" />
                </div>
                <h3 className="text-lg font-semibold text-gray-900">确认删除</h3>
              </div>
              <p className="text-gray-600 mb-6">
                确定要删除此监控目标吗？删除后 Agent 将不再监控该目标。
              </p>
              <div className="flex justify-end space-x-3">
                <button
                  onClick={() => setDeletingTargetId(null)}
                  className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
                >
                  取消
                </button>
                <button
                  onClick={handleDeleteTarget}
                  disabled={deleting}
                  className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 flex items-center space-x-2"
                >
                  {deleting && <Loader2 className="h-4 w-4 animate-spin" />}
                  <span>{deleting ? '删除中...' : '确认删除'}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ====== 自动发现 Modal ====== */}
      {showDiscoverModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b">
              <div className="flex items-center space-x-3">
                <Wand2 className="h-5 w-5 text-primary-600" />
                <h3 className="text-lg font-semibold">自动发现目标</h3>
              </div>
              <button
                onClick={() => setShowDiscoverModal(false)}
                className="p-1 hover:bg-gray-100 rounded"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-6">
              {discovering ? (
                <div className="text-center py-8">
                  <Loader2 className="h-12 w-12 text-primary-600 animate-spin mx-auto mb-4" />
                  <p className="text-gray-600">正在扫描 Agent 上的 SSL 证书...</p>
                </div>
              ) : discoverResult ? (
                <div>
                  {discoverResult.error ? (
                    <div className="text-center py-8">
                      <AlertCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
                      <p className="text-red-600">{discoverResult.error}</p>
                    </div>
                  ) : (
                    <div>
                      {/* 显示总体结果 */}
                      <p className="text-green-600 mb-4 font-medium">
                        扫描完成：新增 {discoverResult.found} 个目标
                      </p>
                      
                      {/* 显示分组结果（如果有） */}
                      {discoverResult.results && discoverResult.results.length > 0 && (
                        <div className="mb-6">
                          <h4 className="text-sm font-medium text-gray-700 mb-2">Agent 扫描结果</h4>
                          <div className="max-h-60 overflow-y-auto border rounded-lg">
                            <table className="min-w-full">
                              <thead className="bg-gray-50">
                                <tr>
                                  <th className="text-left px-4 py-2 text-sm">Agent</th>
                                  <th className="text-left px-4 py-2 text-sm">来源</th>
                                  <th className="text-left px-4 py-2 text-sm">状态</th>
                                  <th className="text-left px-4 py-2 text-sm">新增</th>
                                  <th className="text-left px-4 py-2 text-sm">更新</th>
                                  <th className="text-left px-4 py-2 text-sm">说明</th>
                                </tr>
                              </thead>
                              <tbody>
                                {discoverResult.results.map((r, idx) => (
                                  <tr key={idx} className="border-t">
                                    <td className="px-4 py-2 text-sm font-medium">{r.agent_name || r.agent_id?.substring(0, 8)}</td>
                                    <td className="px-4 py-2 text-sm">
                                      <span className={`px-2 py-0.5 rounded text-xs ${
                                        r.source === 'heartbeat' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600'
                                      }`}>
                                        {r.source === 'heartbeat' ? '心跳' : '拉取'}
                                      </span>
                                    </td>
                                    <td className="px-4 py-2 text-sm">
                                      <span className={`px-2 py-0.5 rounded text-xs ${
                                        r.status === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                                      }`}>
                                        {r.status === 'success' ? '成功' : '失败'}
                                      </span>
                                    </td>
                                    <td className="px-4 py-2 text-sm text-green-600 font-medium">{r.added}</td>
                                    <td className="px-4 py-2 text-sm text-blue-600">{r.updated}</td>
                                    <td className="px-4 py-2 text-sm text-gray-500">{r.message || '-'}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                      
                      {/* 显示发现的目标列表（如果有） */}
                      {discoverResult.targets && discoverResult.targets.length > 0 ? (
                        <div>
                          <h4 className="text-sm font-medium text-gray-700 mb-2">发现的目标列表</h4>
                          <div className="max-h-60 overflow-y-auto border rounded-lg">
                            <table className="min-w-full">
                              <thead className="bg-gray-50">
                                <tr>
                                  <th className="text-left px-4 py-2 text-sm">URL</th>
                                  <th className="text-left px-4 py-2 text-sm">服务名</th>
                                  <th className="text-left px-4 py-2 text-sm">Agent</th>
                                </tr>
                              </thead>
                              <tbody>
                                {discoverResult.targets.map((target, idx) => (
                                  <tr key={idx} className="border-t">
                                    <td className="px-4 py-2 text-sm">{target.url}</td>
                                    <td className="px-4 py-2 text-sm">{target.service_name || '-'}</td>
                                    <td className="px-4 py-2 text-sm">{target.agent_id?.substring(0, 8) || '-'}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ) : discoverResult.found === 0 ? (
                        <div className="text-center py-8">
                          <Search className="h-12 w-12 text-gray-400 mx-auto mb-4" />
                          <p className="text-gray-600">未发现新的 SSL 证书目标</p>
                        </div>
                      ) : null}
                    </div>
                  )}
                  <div className="mt-6 flex justify-center">
                    <button
                      onClick={() => {
                        setShowDiscoverModal(false);
                        setDiscoverResult(null);
                        fetchTargets();
                        fetchStats();
                      }}
                      className="btn-secondary"
                    >
                      关闭
                    </button>
                  </div>
                </div>
              ) : (
                <div className="text-center py-4">
                  <p className="text-gray-600 mb-4">
                    自动发现所有 Agent 上的 SSL 证书监控目标。Push 模式从心跳获取，Pull/Dual 模式主动连接扫描。
                  </p>
                  <div className="mb-4">
                    <button
                      onClick={handleDiscoverAll}
                      className="btn-primary flex items-center space-x-2 mx-auto"
                    >
                      <Wand2 className="h-4 w-4" />
                      <span>发现所有 Agent</span>
                    </button>
                  </div>
                  <div className="relative my-4">
                    <div className="absolute inset-0 flex items-center">
                      <div className="w-full border-t border-gray-300"></div>
                    </div>
                    <div className="relative flex justify-center text-sm">
                      <span className="px-2 bg-white text-gray-500">或选择单个 Agent</span>
                    </div>
                  </div>
                  <div className="mb-4">
                    {agents.length === 0 ? (
                      <p className="text-gray-500">暂无可用的 Agent</p>
                    ) : (
                      <div className="space-y-2 max-h-40 overflow-y-auto">
                        {agents.map((agent) => {
                          const isPushOnly = agent.agent_mode === 'push' || (agent.push_mode && agent.agent_mode !== 'dual');
                          return (
                            <button
                              key={agent.agent_id}
                              onClick={() => handleDiscoverTargets(agent.agent_id)}
                              className="w-full px-4 py-2 text-left border rounded-lg hover:bg-gray-50 flex items-center justify-between"
                            >
                              <span>
                                <span className="font-medium">{agent.hostname || agent.ip}</span>
                                <span className="text-gray-500 text-sm ml-2">{agent.agent_id.substring(0, 8)}...</span>
                                <span className={`ml-2 text-xs px-2 py-0.5 rounded ${
                                  agent.agent_mode === 'dual'
                                    ? 'bg-purple-100 text-purple-700'
                                    : isPushOnly
                                      ? 'bg-blue-100 text-blue-700'
                                      : 'bg-gray-100 text-gray-600'
                                }`}>
                                  {agent.agent_mode === 'dual' ? '双模式' : isPushOnly ? '推送(心跳)' : '拉取'}
                                </span>
                              </span>
                              <span className={`text-xs px-2 py-0.5 rounded ${
                                agent.status === 'online' 
                                  ? 'bg-green-100 text-green-700' 
                                  : 'bg-yellow-100 text-yellow-700'
                              }`}>
                                {agent.status === 'online' ? '在线' : '离线'}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => setShowDiscoverModal(false)}
                    className="btn-secondary"
                  >
                    取消
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

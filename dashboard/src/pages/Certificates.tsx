import React, { useState, useEffect } from 'react';
import { Search, Filter, RefreshCw, CheckCircle, AlertTriangle, XCircle, ChevronDown, ChevronUp, Shield, ShieldOff, Download, WifiOff } from 'lucide-react';
import { SSLCertData } from '../types';
import { fetchMetrics, filterCertificates, getUniqueValues } from '../utils/metrics';
import * as XLSX from 'xlsx';

const STATUS_CONFIG = {
  valid: { label: '正常', color: '#10b981', bgColor: 'bg-green-100', textColor: 'text-green-800' },
  warning: { label: '即将过期', color: '#f59e0b', bgColor: 'bg-amber-100', textColor: 'text-amber-800' },
  critical: { label: '紧急', color: '#ef4444', bgColor: 'bg-red-100', textColor: 'text-red-800' },
  expired: { label: '已过期', color: '#6b7280', bgColor: 'bg-gray-100', textColor: 'text-gray-800' },
  unreachable: { label: '无法连接', color: '#6b7280', bgColor: 'bg-gray-100', textColor: 'text-gray-600' },
};

// 状态排序优先级：紧急 > 即将过期 > 已过期 > 无法连接 > 正常
const STATUS_PRIORITY: Record<string, number> = {
  critical: 0,
  warning: 1,
  expired: 2,
  unreachable: 3,
  valid: 4,
};

const EXPORT_HEADERS = ['服务名称', '主机', '端口', '团队', '环境', '状态', '剩余天数', '到期日期', '生效日期', '主题CN', '颁发者CN', '颁发者组织', 'WebTrust认证', 'SAN数量', '序列号'];

function getExportRows(data: SSLCertData[]) {
  return data.map(cert => [
    cert.service_name,
    cert.hostname,
    cert.port,
    cert.owner,
    cert.env,
    STATUS_CONFIG[cert.status]?.label || cert.status,
    cert.days_left,
    cert.not_after_date,
    cert.not_before_date,
    cert.subject_cn || '',
    cert.issuer_cn || '',
    cert.issuer_org || '',
    cert.is_webtrust ? '是' : '否',
    cert.sans_count,
    cert.serial || '',
  ]);
}

function exportToCSV(data: SSLCertData[], filename: string) {
  const BOM = '\uFEFF';
  const rows = getExportRows(data);
  const csvContent = BOM + [EXPORT_HEADERS, ...rows].map(row =>
    row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')
  ).join('\n');

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function exportToXLSX(data: SSLCertData[], filename: string) {
  const rows = getExportRows(data);
  const wsData = [EXPORT_HEADERS, ...rows];
  const ws = XLSX.utils.aoa_to_sheet(wsData);

  // 设置列宽
  ws['!cols'] = [
    { wch: 20 }, { wch: 25 }, { wch: 8 }, { wch: 10 }, { wch: 10 },
    { wch: 10 }, { wch: 10 }, { wch: 14 }, { wch: 14 }, { wch: 25 },
    { wch: 25 }, { wch: 20 }, { wch: 12 }, { wch: 10 }, { wch: 30 },
  ];

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'SSL证书');
  XLSX.writeFile(wb, filename);
}

export default function Certificates() {
  const [data, setData] = useState<SSLCertData[]>([]);
  const [filteredData, setFilteredData] = useState<SSLCertData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [ownerFilter, setOwnerFilter] = useState('');
  const [envFilter, setEnvFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [showExportMenu, setShowExportMenu] = useState(false);
  
  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const metrics = await fetchMetrics();
      setData(metrics);
      setFilteredData(metrics);
    } catch (err) {
      setError('获取数据失败，请检查网络连接');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    loadData();
  }, []);
  
  useEffect(() => {
    const filtered = filterCertificates(data, {
      search,
      owner: ownerFilter,
      env: envFilter,
      status: statusFilter,
    });
    // 按状态优先级排序：紧急 > 即将过期 > 已过期 > 正常，同状态按剩余天数升序
    const sorted = [...filtered].sort((a, b) => {
      const priorityDiff = (STATUS_PRIORITY[a.status] ?? 99) - (STATUS_PRIORITY[b.status] ?? 99);
      if (priorityDiff !== 0) return priorityDiff;
      return a.days_left - b.days_left;
    });
    setFilteredData(sorted);
  }, [search, ownerFilter, envFilter, statusFilter, data]);
  
  const owners = getUniqueValues(data, 'owner');
  const envs = getUniqueValues(data, 'env');
  
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'valid':
        return <CheckCircle className="h-4 w-4 text-green-500" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-amber-500" />;
      case 'critical':
      case 'expired':
        return <XCircle className="h-4 w-4 text-red-500" />;
      case 'unreachable':
        return <WifiOff className="h-4 w-4 text-gray-400" />;
      default:
        return null;
    }
  };
  
  if (loading && !data.length) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <RefreshCw className="h-12 w-12 text-primary-600 animate-spin mx-auto mb-4" />
          <p className="text-gray-500">加载中...</p>
        </div>
      </div>
    );
  }
  
  if (error && !data.length) {
    return (
      <div className="card text-center py-12">
        <XCircle className="h-16 w-16 text-red-500 mx-auto mb-4" />
        <h2 className="text-xl font-semibold text-gray-900 mb-2">加载失败</h2>
        <p className="text-gray-500 mb-6">{error}</p>
        <button onClick={loadData} className="btn-primary">
          重试
        </button>
      </div>
    );
  }
  
  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">证书列表</h1>
          <p className="text-gray-500 mt-1">
            共 {filteredData.length} 个证书
          </p>
        </div>
        <button onClick={loadData} className="btn-secondary flex items-center space-x-2">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          <span>刷新</span>
        </button>
        <div className="relative">
          <button
            onClick={() => setShowExportMenu(!showExportMenu)}
            className="btn-primary flex items-center space-x-2"
            disabled={filteredData.length === 0}
          >
            <Download className="h-4 w-4" />
            <span>批量导出</span>
            <ChevronDown className="h-3 w-3" />
          </button>
          {showExportMenu && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setShowExportMenu(false)} />
              <div className="absolute right-0 mt-2 w-40 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-20">
                <button
                  onClick={() => {
                    exportToCSV(filteredData, `ssl-certificates-${new Date().toISOString().slice(0, 10)}.csv`);
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 flex items-center space-x-2"
                >
                  <span>导出 CSV</span>
                </button>
                <button
                  onClick={() => {
                    exportToXLSX(filteredData, `ssl-certificates-${new Date().toISOString().slice(0, 10)}.xlsx`);
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 flex items-center space-x-2"
                >
                  <span>导出 XLSX</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
      
      {/* 搜索和筛选 */}
      <div className="card">
        <div className="flex flex-col lg:flex-row gap-4">
          {/* 搜索框 */}
          <div className="flex-1">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-gray-400" />
              <input
                type="text"
                placeholder="搜索服务名、主机名或负责人..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="input pl-10"
              />
            </div>
          </div>
          
          {/* 筛选按钮 */}
          <button
            onClick={() => setShowFilters(!showFilters)}
            className="btn-secondary flex items-center space-x-2"
          >
            <Filter className="h-4 w-4" />
            <span>筛选</span>
            {(ownerFilter || envFilter || statusFilter) && (
              <span className="bg-primary-600 text-white text-xs rounded-full px-2 py-0.5">
                {[ownerFilter, envFilter, statusFilter].filter(Boolean).length}
              </span>
            )}
          </button>
        </div>
        
        {/* 筛选选项 */}
        {showFilters && (
          <div className="mt-4 pt-4 border-t border-gray-200 grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">团队</label>
              <select
                value={ownerFilter}
                onChange={(e) => setOwnerFilter(e.target.value)}
                className="input"
              >
                <option value="">全部</option>
                {owners.map(owner => (
                  <option key={owner} value={owner}>{owner}</option>
                ))}
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">环境</label>
              <select
                value={envFilter}
                onChange={(e) => setEnvFilter(e.target.value)}
                className="input"
              >
                <option value="">全部</option>
                {envs.map(env => (
                  <option key={env} value={env}>{env}</option>
                ))}
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">状态</label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="input"
              >
                <option value="">全部</option>
                <option value="valid">正常</option>
                <option value="warning">即将过期</option>
                <option value="critical">紧急</option>
                <option value="expired">已过期</option>
                <option value="unreachable">无法连接</option>
              </select>
            </div>
          </div>
        )}
        
        {/* 清除筛选 */}
        {(ownerFilter || envFilter || statusFilter) && (
          <div className="mt-4 pt-4 border-t border-gray-200 flex items-center justify-between">
            <p className="text-sm text-gray-500">
              已应用筛选条件
            </p>
            <button
              onClick={() => {
                setOwnerFilter('');
                setEnvFilter('');
                setStatusFilter('');
              }}
              className="text-sm text-primary-600 hover:text-primary-700"
            >
              清除所有筛选
            </button>
          </div>
        )}
      </div>
      
      {/* 证书列表 */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  状态
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  服务名称
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  主机
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  团队
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  环境
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  WebTrust
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  剩余天数
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  到期日期
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {filteredData.map((cert) => {
                const config = STATUS_CONFIG[cert.status];
                const rowKey = `${cert.hostname}:${cert.port}`;
                const isExpanded = expandedRow === rowKey;
                
                return (
                  <React.Fragment key={rowKey}>
                    <tr className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className={`inline-flex items-center space-x-1 px-2 py-1 rounded-full text-xs font-medium ${config.bgColor} ${config.textColor}`}>
                          {getStatusIcon(cert.status)}
                          <span>{config.label}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <p className="font-medium text-gray-900">{cert.service_name}</p>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <p className="text-gray-900">{cert.hostname}</p>
                        <p className="text-gray-500 text-sm">:{cert.port}</p>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-gray-900">
                        {cert.owner}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${
                          cert.env === 'production' 
                            ? 'bg-blue-100 text-blue-800' 
                            : 'bg-gray-100 text-gray-800'
                        }`}>
                          {cert.env}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        {cert.is_webtrust ? (
                          <div className="flex items-center space-x-1 text-green-600" title={`颁发者: ${cert.issuer_org || cert.issuer_cn}`}>
                            <Shield className="h-5 w-5" />
                            <span className="text-sm font-medium">已认证</span>
                          </div>
                        ) : (
                          <div className="flex items-center space-x-1 text-gray-400" title={cert.issuer_org || '未知颁发者'}>
                            <ShieldOff className="h-5 w-5" />
                            <span className="text-sm">未认证</span>
                          </div>
                        )}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <p className={`font-bold ${
                          cert.days_left <= 7 ? 'text-red-600' :
                          cert.days_left <= 30 ? 'text-amber-600' :
                          'text-gray-900'
                        }`}>
                          {cert.days_left}
                        </p>
                        <p className="text-gray-500 text-sm">天</p>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-gray-900">
                        {cert.not_after_date}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <button
                          onClick={() => setExpandedRow(isExpanded ? null : rowKey)}
                          className="text-primary-600 hover:text-primary-700 flex items-center space-x-1"
                        >
                          <span>{isExpanded ? '收起' : '详情'}</span>
                          {isExpanded ? (
                            <ChevronUp className="h-4 w-4" />
                          ) : (
                            <ChevronDown className="h-4 w-4" />
                          )}
                        </button>
                      </td>
                    </tr>
                    
                    {/* 展开的详情行 */}
                    {isExpanded && (
                      <tr>
                        <td colSpan={9} className="px-6 py-4 bg-gray-50">
                          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                            <div>
                              <h4 className="text-sm font-medium text-gray-700 mb-2">证书信息</h4>
                              <div className="space-y-2 text-sm">
                                <p><span className="text-gray-500">主题CN:</span> <span className="font-mono">{cert.subject_cn || '-'}</span></p>
                                <p><span className="text-gray-500">颁发者CN:</span> <span className="font-mono">{cert.issuer_cn || '-'}</span></p>
                                <p><span className="text-gray-500">颁发者组织:</span> <span className="font-mono">{cert.issuer_org || '-'}</span></p>
                                <p><span className="text-gray-500">序列号:</span> <span className="font-mono text-xs">{cert.serial || '-'}</span></p>
                                <p><span className="text-gray-500">SAN数量:</span> {cert.sans_count}</p>
                              </div>
                            </div>
                            
                            <div>
                              <h4 className="text-sm font-medium text-gray-700 mb-2">有效期</h4>
                              <div className="space-y-2 text-sm">
                                <p><span className="text-gray-500">生效时间:</span> {cert.not_before_date}</p>
                                <p><span className="text-gray-500">到期时间:</span> {cert.not_after_date}</p>
                                <p><span className="text-gray-500">剩余天数:</span> {cert.days_left} 天</p>
                              </div>
                            </div>
                            
                            <div>
                              <h4 className="text-sm font-medium text-gray-700 mb-2">更多信息</h4>
                              <div className="space-y-2 text-sm">
                                <p><span className="text-gray-500">环境:</span> {cert.env}</p>
                                <p><span className="text-gray-500">团队:</span> {cert.owner}</p>
                                <p>
                                  <span className="text-gray-500">验证状态:</span>
                                  <span className={cert.check_success ? 'text-green-600' : 'text-red-600'}>
                                    {cert.check_success ? ' 通过' : ' 失败'}
                                  </span>
                                </p>
                                <p>
                                  <span className="text-gray-500">WebTrust认证:</span>
                                  {cert.is_webtrust ? (
                                    <span className="inline-flex items-center ml-1 text-green-600">
                                      <Shield className="h-4 w-4 mr-0.5" /> 是
                                    </span>
                                  ) : (
                                    <span className="inline-flex items-center ml-1 text-gray-400">
                                      <ShieldOff className="h-4 w-4 mr-0.5" /> 否
                                    </span>
                                  )}
                                </p>
                              </div>
                            </div>
                            
                            {cert.subject && (
                              <div className="md:col-span-2 lg:col-span-3">
                                <h4 className="text-sm font-medium text-gray-700 mb-2">证书主题详情</h4>
                                <pre className="bg-gray-100 p-3 rounded text-xs overflow-x-auto">
                                  {(() => {
                                    try {
                                      return JSON.stringify(JSON.parse(cert.subject), null, 2);
                                    } catch {
                                      return cert.subject;
                                    }
                                  })()}
                                </pre>
                              </div>
                            )}
                            
                            {cert.issuer && (
                              <div className="md:col-span-2 lg:col-span-3">
                                <h4 className="text-sm font-medium text-gray-700 mb-2">颁发者详情</h4>
                                <pre className="bg-gray-100 p-3 rounded text-xs overflow-x-auto">
                                  {(() => {
                                    try {
                                      return JSON.stringify(JSON.parse(cert.issuer), null, 2);
                                    } catch {
                                      return cert.issuer;
                                    }
                                  })()}
                                </pre>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          
          {filteredData.length === 0 && (
            <div className="text-center py-12">
              <p className="text-gray-500">没有找到匹配的证书</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

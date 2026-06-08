import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import { Shield, LayoutDashboard, FileText, LogOut, RefreshCw, Bell, Settings, Eye, Server, Cog, ChevronLeft, ChevronRight, Menu, Key } from 'lucide-react';
import { logout, getCurrentUser } from '../utils/auth';
import { useState, useEffect } from 'react';

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [refreshing, setRefreshing] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  
  useEffect(() => {
    const user = getCurrentUser();
    setIsAdmin(user?.role === 'admin');
  }, []);
  
  const handleLogout = () => {
    logout();
    navigate('/login');
  };
  
  const refreshPage = () => {
    setRefreshing(true);
    window.location.reload();
    setTimeout(() => setRefreshing(false), 1000);
  };
  
  const navItems = [
    { path: '/dashboard', label: '仪表盘', icon: LayoutDashboard },
    { path: '/certificates', label: '证书列表', icon: FileText },
    { path: '/alerts', label: '告警管理', icon: Bell },
    ...(isAdmin ? [
      { path: '/targets', label: '目标管理', icon: Settings },
      { path: '/agent-targets', label: 'Agent目标', icon: Server },
      { path: '/credential-manager', label: '凭证管理', icon: Key },
      { path: '/settings', label: '系统设置', icon: Cog }
    ] : []),
  ];

  const sidebarWidth = collapsed ? 'w-16' : 'w-56';

  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* 移动端遮罩 */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* 左侧菜单 */}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 ${sidebarWidth} min-h-screen bg-gradient-to-b from-slate-900 to-slate-800 text-white flex flex-col transition-all duration-300 transform ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        }`}
      >
        {/* Logo 区域 */}
        <div className={`flex items-center ${collapsed ? 'justify-center py-5' : 'space-x-3 px-5 py-5'} border-b border-slate-700/50`}>
          <Shield className="h-8 w-8 text-sky-400 flex-shrink-0" />
          {!collapsed && (
            <span className="text-lg font-bold tracking-wide whitespace-nowrap">SSL证书监控</span>
          )}
        </div>

        {/* 导航菜单 */}
        <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto">
          {navItems.map(item => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path;
            return (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setMobileOpen(false)}
                className={`flex items-center ${collapsed ? 'justify-center' : 'space-x-3'} px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
                  isActive
                    ? 'bg-sky-500/20 text-sky-300 shadow-sm'
                    : 'text-slate-300 hover:bg-slate-700/50 hover:text-white'
                }`}
                title={collapsed ? item.label : undefined}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {!collapsed && <span>{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        {/* 底部区域 */}
        <div className={`border-t border-slate-700/50 ${collapsed ? 'px-2' : 'px-4'} py-3 space-y-2`}>
          {/* 用户信息 */}
          {!collapsed && (
            <div className="flex items-center space-x-2 px-2 py-1.5">
              <div className="w-7 h-7 rounded-full bg-sky-500/30 flex items-center justify-center">
                <span className="text-xs font-bold text-sky-300">{getCurrentUser()?.username?.[0]?.toUpperCase()}</span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-slate-300 truncate">{getCurrentUser()?.username}</p>
                <p className="text-[10px] text-slate-500">
                  {isAdmin ? '管理员' : '只读用户'}
                </p>
              </div>
              {isAdmin && (
                <span className="px-1.5 py-0.5 text-[10px] font-medium bg-sky-500/20 text-sky-300 rounded">管理</span>
              )}
            </div>
          )}

          {/* 操作按钮 */}
          <div className={`flex ${collapsed ? 'flex-col items-center space-y-1' : 'space-x-2'}`}>
            <button
              onClick={refreshPage}
              className={`${collapsed ? 'p-2' : 'flex items-center space-x-2 px-3 py-2'} text-slate-400 hover:text-white hover:bg-slate-700/50 rounded-lg transition-colors w-full`}
              title="刷新页面"
            >
              <RefreshCw className={`h-4 w-4 flex-shrink-0 ${refreshing ? 'animate-spin' : ''}`} />
              {!collapsed && <span className="text-xs">刷新</span>}
            </button>
            <button
              onClick={handleLogout}
              className={`${collapsed ? 'p-2' : 'flex items-center space-x-2 px-3 py-2'} text-slate-400 hover:text-red-300 hover:bg-slate-700/50 rounded-lg transition-colors w-full`}
              title="退出登录"
            >
              <LogOut className="h-4 w-4 flex-shrink-0" />
              {!collapsed && <span className="text-xs">退出</span>}
            </button>
          </div>
        </div>

        {/* 折叠按钮 (仅桌面端) */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="hidden lg:flex items-center justify-center py-2 border-t border-slate-700/50 text-slate-500 hover:text-white hover:bg-slate-700/50 transition-colors"
          title={collapsed ? '展开菜单' : '收起菜单'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </aside>

      {/* 右侧内容区 */}
      <div className="flex-1 flex flex-col min-h-screen min-w-0">
        {/* 顶部栏 (移动端汉堡 + 面包屑) */}
        <header className="bg-white shadow-sm border-b border-gray-200 px-4 sm:px-6 h-14 flex items-center sticky top-0 z-30">
          {/* 移动端菜单按钮 */}
          <button
            onClick={() => setMobileOpen(true)}
            className="lg:hidden p-2 -ml-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg mr-3"
          >
            <Menu className="h-5 w-5" />
          </button>

          {/* 面包屑 */}
          <div className="flex items-center space-x-2 text-sm text-gray-500">
            <Shield className="h-4 w-4 text-sky-500" />
            <span>/</span>
            <span className="text-gray-900 font-medium">
              {navItems.find(item => item.path === location.pathname)?.label || '页面'}
            </span>
          </div>

          {/* 右侧操作区 */}
          <div className="ml-auto flex items-center space-x-2">
            {isAdmin && (
              <span className="hidden sm:inline-flex items-center px-2 py-0.5 text-xs font-medium bg-primary-100 text-primary-700 rounded">
                <Eye className="h-3 w-3 mr-1" />
                管理员
              </span>
            )}
          </div>
        </header>

        {/* 主内容区 */}
        <main className="flex-1 p-4 sm:p-6 lg:p-8 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

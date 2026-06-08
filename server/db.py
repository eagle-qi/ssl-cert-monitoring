#!/usr/bin/env python3
"""
PostgreSQL (IvorySQL) 数据库抽象层
替代原有的 SQLite 方案，提供统一的数据库操作接口
IvorySQL 完全兼容 PostgreSQL 协议，使用 psycopg2 驱动
"""

import psycopg2
import psycopg2.extras
import json
import os
import uuid
import logging
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 数据库连接配置
DB_HOST = os.getenv('DB_HOST', 'ivorysql')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'ssl_monitor')
DB_USER = os.getenv('DB_USER', 'ssl_monitor')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'ssl_monitor_pass')

# 数据库版本
DB_VERSION = 1

# 指标保留天数
METRICS_RETENTION_DAYS = int(os.getenv('METRICS_RETENTION_DAYS', '30'))


def _get_dsn():
    """获取数据库连接 DSN"""
    return (
        f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
        f"user={DB_USER} password={DB_PASSWORD}"
    )


def _get_conn():
    """获取数据库连接"""
    conn = psycopg2.connect(_get_dsn())
    conn.autocommit = False
    return conn


@contextmanager
def get_db():
    """数据库连接上下文管理器"""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """初始化数据库，创建所有表"""
    with get_db() as conn:
        cur = conn.cursor()

        # ========== 系统元信息 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS _meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        # 检查数据库版本
        cur.execute("SELECT value FROM _meta WHERE key='db_version'")
        row = cur.fetchone()
        current_version = int(row[0]) if row else 0
        if current_version < DB_VERSION:
            cur.execute('''
                INSERT INTO _meta (key, value) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', ('db_version', str(DB_VERSION)))

        # ========== 全局设置表 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        # ========== Agent 注册表 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS agents (
                agent_id    TEXT PRIMARY KEY,
                host        TEXT NOT NULL DEFAULT '',
                port        INTEGER DEFAULT 8091,
                name        TEXT DEFAULT '',
                enabled     BOOLEAN DEFAULT TRUE,
                use_https   BOOLEAN DEFAULT FALSE,
                push_mode   BOOLEAN DEFAULT FALSE,
                agent_mode  TEXT DEFAULT 'push',
                auto_registered BOOLEAN DEFAULT FALSE,
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            )
        ''')

        # ========== 监控目标表 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS targets (
                id              TEXT PRIMARY KEY,
                url             TEXT NOT NULL,
                service_name    TEXT DEFAULT '',
                owner           TEXT DEFAULT '',
                owner_email     TEXT DEFAULT '',
                env             TEXT DEFAULT 'production',
                enabled         BOOLEAN DEFAULT TRUE,
                check_interval  INTEGER DEFAULT 180,
                timeout         INTEGER DEFAULT 30,
                agent_id        TEXT DEFAULT '',
                synced_from_agent BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP DEFAULT NOW(),
                updated_at      TIMESTAMP DEFAULT NOW(),
                UNIQUE(url, agent_id)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_targets_agent_id ON targets(agent_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_targets_enabled ON targets(enabled)')

        # ========== 凭证管理表 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS credentials (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                type         TEXT NOT NULL,
                type_label   TEXT DEFAULT '',
                expiry_date  TEXT DEFAULT '',
                owner        TEXT DEFAULT '',
                owner_email  TEXT DEFAULT '',
                env          TEXT DEFAULT 'production',
                service_name TEXT DEFAULT '',
                remark       TEXT DEFAULT '',
                enabled      BOOLEAN DEFAULT TRUE,
                notify_days  INTEGER DEFAULT 30,
                created_at   TIMESTAMP DEFAULT NOW(),
                updated_at   TIMESTAMP DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_credentials_type ON credentials(type)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_credentials_enabled ON credentials(enabled)')

        # ========== 指标历史表 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS metrics (
                id           BIGSERIAL PRIMARY KEY,
                timestamp    TIMESTAMP NOT NULL DEFAULT NOW(),
                metric_type  TEXT NOT NULL,
                hostname     TEXT DEFAULT '',
                port         TEXT DEFAULT '',
                service_name TEXT DEFAULT '',
                owner        TEXT DEFAULT '',
                owner_email  TEXT DEFAULT '',
                env          TEXT DEFAULT 'production',
                value        DOUBLE PRECISION NOT NULL,
                agent_id     TEXT DEFAULT '',
                agent_name   TEXT DEFAULT '',
                agent_hostname TEXT DEFAULT '',
                source       TEXT DEFAULT 'direct',
                subject_cn   TEXT DEFAULT '',
                issuer_cn    TEXT DEFAULT '',
                issuer_org   TEXT DEFAULT '',
                subject_json TEXT DEFAULT '',
                issuer_json  TEXT DEFAULT '',
                serial       TEXT DEFAULT ''
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_metrics_hostname_port ON metrics(hostname, port)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_metrics_type ON metrics(metric_type)')

        # ========== Agent 心跳表 ==========
        cur.execute('''
            CREATE TABLE IF NOT EXISTS agent_heartbeats (
                agent_id       TEXT PRIMARY KEY,
                last_heartbeat TEXT NOT NULL,
                agent_info     TEXT DEFAULT '{}',
                targets_count  INTEGER DEFAULT 0,
                metrics_buffer_size INTEGER DEFAULT 0,
                push_queue_size INTEGER DEFAULT 0,
                push_mode     BOOLEAN DEFAULT FALSE,
                agent_mode    TEXT DEFAULT 'push',
                scrape_interval INTEGER DEFAULT 180,
                local_targets TEXT DEFAULT '[]',
                updated_at    TIMESTAMP DEFAULT NOW()
            )
        ''')

        cur.close()

    logger.info(f"数据库初始化完成: {DB_HOST}:{DB_PORT}/{DB_NAME}")


# ============================================================
#  辅助函数
# ============================================================

def _row_to_dict(row):
    """将 psycopg2 的 tuple 行转为 dict"""
    if row is None:
        return None
    # row 是 RealDictCursor 返回的 dict，或者是 tuple
    if isinstance(row, dict):
        d = dict(row)
    else:
        return None
    # 将布尔字段处理（PostgreSQL 原生支持 BOOLEAN，不需要转换）
    # 但为了与前端兼容，保持 enabled 等字段返回 true/false
    bool_fields = {'enabled', 'use_https', 'push_mode', 'auto_registered',
                   'synced_from_agent'}
    for k in bool_fields:
        if k in d and d[k] is not None:
            d[k] = bool(d[k])
    # 将 datetime 对象转为 ISO 格式字符串（兼容前端）
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ============================================================
#  Agent CRUD
# ============================================================

def get_agents():
    """获取所有 Agent 配置"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM agents ORDER BY created_at")
        rows = cur.fetchall()
        cur.close()
        return [_row_to_dict(r) for r in rows]


def get_agent(agent_id):
    """获取单个 Agent"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM agents WHERE agent_id=%s", (agent_id,))
        row = cur.fetchone()
        cur.close()
        return _row_to_dict(row) if row else None


def upsert_agent(agent: dict):
    """新增或更新 Agent"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM agents WHERE agent_id=%s", (agent['agent_id'],))
        existing = cur.fetchone()
        now = datetime.now().isoformat()
        if existing:
            cur.execute('''
                UPDATE agents SET host=%s, port=%s, name=%s, enabled=%s,
                    use_https=%s, push_mode=%s, agent_mode=%s, updated_at=%s
                WHERE agent_id=%s
            ''', (
                agent.get('host', ''),
                agent.get('port', 8091),
                agent.get('name', ''),
                agent.get('enabled', True),
                agent.get('use_https', False),
                agent.get('push_mode', False),
                agent.get('agent_mode', 'push'),
                now,
                agent['agent_id'],
            ))
        else:
            cur.execute('''
                INSERT INTO agents (agent_id, host, port, name, enabled, use_https,
                    push_mode, agent_mode, auto_registered, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                agent['agent_id'],
                agent.get('host', ''),
                agent.get('port', 8091),
                agent.get('name', ''),
                agent.get('enabled', True),
                agent.get('use_https', False),
                agent.get('push_mode', False),
                agent.get('agent_mode', 'push'),
                agent.get('auto_registered', False),
                now, now,
            ))
        cur.close()


def delete_agent(agent_id):
    """删除 Agent"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM agents WHERE agent_id=%s", (agent_id,))
        cur.execute("DELETE FROM targets WHERE agent_id=%s", (agent_id,))
        cur.execute("DELETE FROM agent_heartbeats WHERE agent_id=%s", (agent_id,))
        cur.close()


# ============================================================
#  Settings CRUD
# ============================================================

def get_setting(key, default=None):
    """获取设置项"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
        row = cur.fetchone()
        cur.close()
        return row['value'] if row else default


def set_setting(key, value):
    """设置项"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (key, value))
        cur.close()


def get_all_settings():
    """获取所有设置"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT key, value FROM settings")
        rows = cur.fetchall()
        cur.close()
        return {r['key']: r['value'] for r in rows}


# ============================================================
#  Targets CRUD
# ============================================================

def get_targets(agent_id=None, enabled_only=False):
    """获取目标列表"""
    with get_db() as conn:
        sql = "SELECT * FROM targets WHERE 1=1"
        params = []
        if agent_id is not None:
            if agent_id == '':
                sql += " AND (agent_id='' OR agent_id IS NULL)"
            else:
                sql += " AND agent_id=%s"
                params.append(agent_id)
        if enabled_only:
            sql += " AND enabled=TRUE"
        sql += " ORDER BY created_at"
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [_row_to_dict(r) for r in rows]


def get_target(target_id):
    """获取单个目标"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM targets WHERE id=%s", (target_id,))
        row = cur.fetchone()
        cur.close()
        return _row_to_dict(row) if row else None


def get_target_by_url(url, agent_id=''):
    """根据 URL 和 agent_id 查找目标"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM targets WHERE url=%s AND agent_id=%s",
                    (url, agent_id))
        row = cur.fetchone()
        cur.close()
        return _row_to_dict(row) if row else None


def upsert_target(target: dict):
    """新增或更新目标"""
    with get_db() as conn:
        cur = conn.cursor()
        tid = target.get('id', '')
        now = datetime.now().isoformat()

        if tid:
            cur.execute("SELECT 1 FROM targets WHERE id=%s", (tid,))
            existing = cur.fetchone()
            if existing:
                cur.execute('''
                    UPDATE targets SET url=%s, service_name=%s, owner=%s, owner_email=%s,
                        env=%s, enabled=%s, check_interval=%s, timeout=%s, agent_id=%s,
                        synced_from_agent=%s, updated_at=%s
                    WHERE id=%s
                ''', (
                    target.get('url', ''),
                    target.get('service_name', ''),
                    target.get('owner', ''),
                    target.get('owner_email', ''),
                    target.get('env', 'production'),
                    target.get('enabled', True),
                    target.get('check_interval', 180),
                    target.get('timeout', 30),
                    target.get('agent_id', ''),
                    target.get('synced_from_agent', False),
                    now, tid,
                ))
                cur.close()
                return

        # 新增 - 生成 ID
        if not tid:
            tid = str(uuid.uuid4())

        try:
            cur.execute('''
                INSERT INTO targets (id, url, service_name, owner, owner_email,
                    env, enabled, check_interval, timeout, agent_id,
                    synced_from_agent, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                tid,
                target.get('url', ''),
                target.get('service_name', ''),
                target.get('owner', ''),
                target.get('owner_email', ''),
                target.get('env', 'production'),
                target.get('enabled', True),
                target.get('check_interval', 180),
                target.get('timeout', 30),
                target.get('agent_id', ''),
                target.get('synced_from_agent', False),
                now, now,
            ))
        except psycopg2.errors.UniqueViolation:
            # url+agent_id 唯一约束冲突 → 更新
            conn.rollback()
            cur = conn.cursor()
            cur.execute('''
                UPDATE targets SET service_name=%s, owner=%s, owner_email=%s,
                    env=%s, enabled=%s, check_interval=%s, timeout=%s,
                    synced_from_agent=%s, updated_at=%s
                WHERE url=%s AND agent_id=%s
            ''', (
                target.get('service_name', ''),
                target.get('owner', ''),
                target.get('owner_email', ''),
                target.get('env', 'production'),
                target.get('enabled', True),
                target.get('check_interval', 180),
                target.get('timeout', 30),
                target.get('synced_from_agent', False),
                now,
                target.get('url', ''),
                target.get('agent_id', ''),
            ))
        cur.close()


def delete_target(target_id):
    """删除目标"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM targets WHERE id=%s", (target_id,))
        deleted = cur.rowcount > 0
        cur.close()
        return deleted


def delete_metrics_by_hostname_port(hostname, port):
    """按 hostname:port 删除关联的指标数据"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM metrics WHERE hostname=%s AND port=%s", (hostname, port))
        deleted = cur.rowcount
        cur.close()
        return deleted


def delete_targets_by_agent(agent_id):
    """删除指定 Agent 的所有目标"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM targets WHERE agent_id=%s", (agent_id,))
        cur.close()


# ============================================================
#  Credentials CRUD
# ============================================================

def get_credentials(enabled_only=False):
    """获取所有凭证"""
    with get_db() as conn:
        sql = "SELECT * FROM credentials WHERE 1=1"
        params = []
        if enabled_only:
            sql += " AND enabled=TRUE"
        sql += " ORDER BY created_at"
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [_row_to_dict(r) for r in rows]


def get_credential(cred_id):
    """获取单个凭证"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM credentials WHERE id=%s", (cred_id,))
        row = cur.fetchone()
        cur.close()
        return _row_to_dict(row) if row else None


def upsert_credential(cred: dict):
    """新增或更新凭证"""
    with get_db() as conn:
        cur = conn.cursor()
        cid = cred.get('id', '')
        now = datetime.now().isoformat()

        if cid:
            cur.execute("SELECT 1 FROM credentials WHERE id=%s", (cid,))
            existing = cur.fetchone()
            if existing:
                cur.execute('''
                    UPDATE credentials SET name=%s, type=%s, type_label=%s, expiry_date=%s,
                        owner=%s, owner_email=%s, env=%s, service_name=%s, remark=%s,
                        enabled=%s, notify_days=%s, updated_at=%s
                    WHERE id=%s
                ''', (
                    cred.get('name', ''),
                    cred.get('type', ''),
                    cred.get('type_label', ''),
                    cred.get('expiry_date', ''),
                    cred.get('owner', ''),
                    cred.get('owner_email', ''),
                    cred.get('env', 'production'),
                    cred.get('service_name', ''),
                    cred.get('remark', ''),
                    cred.get('enabled', True),
                    cred.get('notify_days', 30),
                    now, cid,
                ))
                cur.close()
                return

        # 新增
        if not cid:
            cid = str(uuid.uuid4())

        cur.execute('''
            INSERT INTO credentials (id, name, type, type_label, expiry_date,
                owner, owner_email, env, service_name, remark,
                enabled, notify_days, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            cid,
            cred.get('name', ''),
            cred.get('type', ''),
            cred.get('type_label', ''),
            cred.get('expiry_date', ''),
            cred.get('owner', ''),
            cred.get('owner_email', ''),
            cred.get('env', 'production'),
            cred.get('service_name', ''),
            cred.get('remark', ''),
            cred.get('enabled', True),
            cred.get('notify_days', 30),
            now, now,
        ))
        cred['id'] = cid
        cur.close()


def delete_credential(cred_id):
    """删除凭证"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM credentials WHERE id=%s", (cred_id,))
        deleted = cur.rowcount > 0
        cur.close()
        return deleted


# ============================================================
#  Metrics CRUD
# ============================================================

def insert_metrics(metrics_list: list):
    """批量插入指标数据"""
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now().isoformat()
        for m in metrics_list:
            cur.execute('''
                INSERT INTO metrics (timestamp, metric_type, hostname, port,
                    service_name, owner, owner_email, env, value,
                    agent_id, agent_name, agent_hostname, source,
                    subject_cn, issuer_cn, issuer_org, subject_json, issuer_json, serial)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                m.get('timestamp', now),
                m.get('metric_type', m.get('metric_name', 'unknown')),
                m.get('hostname', ''),
                m.get('port', ''),
                m.get('service_name', ''),
                m.get('owner', ''),
                m.get('owner_email', ''),
                m.get('env', 'production'),
                m.get('value', 0),
                m.get('agent_id', ''),
                m.get('agent_name', ''),
                m.get('agent_hostname', ''),
                m.get('source', 'direct'),
                m.get('subject_cn', ''),
                m.get('issuer_cn', ''),
                m.get('issuer_org', ''),
                m.get('subject', '') if not isinstance(m.get('subject', ''), str) else m.get('subject', ''),
                m.get('issuer', '') if not isinstance(m.get('issuer', ''), str) else m.get('issuer', ''),
                m.get('serial', ''),
            ))
        cur.close()


def query_latest_metrics():
    """查询最新指标（每个 hostname:port:metric_type 的最新值，用于 Prometheus /metrics）"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('''
            SELECT m.* FROM metrics m
            INNER JOIN (
                SELECT hostname, port, metric_type, agent_id,
                       MAX(timestamp) as max_ts
                FROM metrics
                WHERE timestamp > NOW() - INTERVAL '%s minutes'
                GROUP BY hostname, port, metric_type, agent_id
            ) latest
            ON m.hostname = latest.hostname
            AND m.port = latest.port
            AND m.metric_type = latest.metric_type
            AND (m.agent_id = latest.agent_id OR (m.agent_id = '' AND latest.agent_id = ''))
            AND m.timestamp = latest.max_ts
        ''', (30,))
        rows = cur.fetchall()
        cur.close()
        return [_row_to_dict(r) for r in rows]


def get_recent_metrics(minutes=30):
    """获取最近 N 分钟的指标"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('''
            SELECT * FROM metrics
            WHERE timestamp > NOW() - INTERVAL '%s minutes'
            ORDER BY timestamp DESC
        ''', (minutes,))
        rows = cur.fetchall()
        cur.close()
        return [_row_to_dict(r) for r in rows]


def get_metrics_count():
    """获取指标总数"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT COUNT(*) as cnt FROM metrics")
        row = cur.fetchone()
        cur.close()
        return row['cnt']


def cleanup_old_metrics():
    """清理过期指标数据"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('''
            DELETE FROM metrics
            WHERE timestamp < NOW() - INTERVAL '%s days'
        ''', (METRICS_RETENTION_DAYS,))
        deleted = cur.rowcount
        cur.close()
        if deleted > 0:
            logger.info(f"清理过期指标: 删除 {deleted} 条 (> {METRICS_RETENTION_DAYS} 天)")
        return deleted


# ============================================================
#  Agent Heartbeats
# ============================================================

def update_heartbeat(agent_id: str, data: dict):
    """更新 Agent 心跳"""
    with get_db() as conn:
        cur = conn.cursor()
        now = datetime.now().isoformat()
        cur.execute('''
            INSERT INTO agent_heartbeats (agent_id, last_heartbeat, agent_info,
                targets_count, metrics_buffer_size, push_queue_size,
                push_mode, agent_mode, scrape_interval, local_targets, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_id) DO UPDATE SET
                last_heartbeat=EXCLUDED.last_heartbeat,
                agent_info=EXCLUDED.agent_info,
                targets_count=EXCLUDED.targets_count,
                metrics_buffer_size=EXCLUDED.metrics_buffer_size,
                push_queue_size=EXCLUDED.push_queue_size,
                push_mode=EXCLUDED.push_mode,
                agent_mode=EXCLUDED.agent_mode,
                scrape_interval=EXCLUDED.scrape_interval,
                local_targets=EXCLUDED.local_targets,
                updated_at=EXCLUDED.updated_at
        ''', (
            agent_id,
            now,
            json.dumps(data.get('agent_info', {}), ensure_ascii=False),
            data.get('targets_count', 0),
            data.get('metrics_buffer_size', 0),
            data.get('push_queue_size', 0),
            data.get('push_mode', False),
            data.get('agent_mode', 'push'),
            data.get('scrape_interval', 180),
            json.dumps(data.get('local_targets', []), ensure_ascii=False),
            now,
        ))
        cur.close()


def get_heartbeat(agent_id: str):
    """获取 Agent 心跳信息"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM agent_heartbeats WHERE agent_id=%s",
                    (agent_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        d = _row_to_dict(row)
        # 解析 JSON 字段
        d['agent_info'] = json.loads(d.get('agent_info', '{}'))
        d['local_targets'] = json.loads(d.get('local_targets', '[]'))
        return d


def get_all_heartbeats():
    """获取所有 Agent 心跳"""
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM agent_heartbeats")
        rows = cur.fetchall()
        cur.close()
        result = {}
        for r in rows:
            d = _row_to_dict(r)
            d['agent_info'] = json.loads(d.get('agent_info', '{}'))
            d['local_targets'] = json.loads(d.get('local_targets', '[]'))
            d['last_heartbeat'] = datetime.fromisoformat(d['last_heartbeat']) if d.get('last_heartbeat') else None
            result[d['agent_id']] = d
        return result


# ============================================================
#  同步辅助：生成 prometheus_targets.json
# ============================================================

def generate_prometheus_targets(output_path: str):
    """从数据库生成 Prometheus targets JSON 文件"""
    targets = get_targets(enabled_only=True)
    prom_targets = []
    for t in targets:
        if not t.get('agent_id'):  # 只导出直接监控目标
            prom_targets.append({
                'targets': [t['url']],
                'labels': {
                    'service_name': t.get('service_name', ''),
                    'owner': t.get('owner', ''),
                    'env': t.get('env', 'production'),
                }
            })
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(prom_targets, f, ensure_ascii=False, indent=2)
    logger.info(f"已生成 Prometheus targets: {output_path}, {len(prom_targets)} 个目标")


# ============================================================
#  同步辅助：生成 agent/data/targets.json
# ============================================================

def generate_agent_targets(output_path: str):
    """从数据库生成 Agent 目标 JSON 文件"""
    targets = get_targets()
    agent_targets = [t for t in targets if t.get('agent_id')]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({'targets': agent_targets}, f, ensure_ascii=False, indent=2)
    logger.info(f"已生成 Agent targets: {output_path}, {len(agent_targets)} 个目标")

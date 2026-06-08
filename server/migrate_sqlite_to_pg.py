#!/usr/bin/env python3
"""
SQLite → PostgreSQL 数据迁移脚本
将旧 SQLite 数据库中的数据迁移到新的 PostgreSQL (IvorySQL) 数据库
"""

import sqlite3
import psycopg2
import psycopg2.extras
import json
import os
import sys

# SQLite 数据源
SQLITE_PATH = os.getenv('SQLITE_PATH', '/app/data/ssl_monitor.db')

# PostgreSQL 目标
PG_HOST = os.getenv('DB_HOST', 'ivorysql')
PG_PORT = int(os.getenv('DB_PORT', '5432'))
PG_NAME = os.getenv('DB_NAME', 'ssl_monitor')
PG_USER = os.getenv('DB_USER', 'ssl_monitor')
PG_PASS = os.getenv('DB_PASSWORD', 'ssl_monitor_pass')


def migrate():
    # 连接 SQLite
    print(f"连接 SQLite: {SQLITE_PATH}")
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row
    sq_conn = sq.cursor()

    # 连接 PostgreSQL
    print(f"连接 PostgreSQL: {PG_HOST}:{PG_PORT}/{PG_NAME}")
    pg = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_NAME,
        user=PG_USER, password=PG_PASS
    )
    pg_conn = pg.cursor()

    # 1. 迁移 settings
    print("\n=== 迁移 settings ===")
    sq_conn.execute("SELECT key, value FROM settings")
    rows = sq_conn.fetchall()
    for r in rows:
        pg_conn.execute('''
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (r['key'], r['value']))
    pg.commit()
    print(f"  ✓ settings: {len(rows)} 条")

    # 2. 迁移 _meta
    print("\n=== 迁移 _meta ===")
    sq_conn.execute("SELECT key, value FROM _meta")
    rows = sq_conn.fetchall()
    for r in rows:
        pg_conn.execute('''
            INSERT INTO _meta (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (r['key'], r['value']))
    pg.commit()
    print(f"  ✓ _meta: {len(rows)} 条")

    # 3. 迁移 agents
    print("\n=== 迁移 agents ===")
    sq_conn.execute("SELECT * FROM agents")
    rows = sq_conn.fetchall()
    cols = [d[0] for d in sq_conn.description]
    for r in rows:
        d = dict(zip(cols, r))
        pg_conn.execute('''
            INSERT INTO agents (agent_id, host, port, name, enabled, use_https,
                push_mode, agent_mode, auto_registered, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_id) DO UPDATE SET
                host=EXCLUDED.host, port=EXCLUDED.port, name=EXCLUDED.name,
                enabled=EXCLUDED.enabled, use_https=EXCLUDED.use_https,
                push_mode=EXCLUDED.push_mode, agent_mode=EXCLUDED.agent_mode,
                updated_at=EXCLUDED.updated_at
        ''', (
            d['agent_id'], d['host'], d['port'], d['name'],
            bool(d['enabled']), bool(d['use_https']), bool(d['push_mode']),
            d['agent_mode'], bool(d['auto_registered']),
            d['created_at'], d['updated_at'],
        ))
    pg.commit()
    print(f"  ✓ agents: {len(rows)} 条")

    # 4. 迁移 targets
    print("\n=== 迁移 targets ===")
    sq_conn.execute("SELECT * FROM targets")
    rows = sq_conn.fetchall()
    cols = [d[0] for d in sq_conn.description]
    for r in rows:
        d = dict(zip(cols, r))
        pg_conn.execute('''
            INSERT INTO targets (id, url, service_name, owner, owner_email,
                env, enabled, check_interval, timeout, agent_id,
                synced_from_agent, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                url=EXCLUDED.url, service_name=EXCLUDED.service_name,
                owner=EXCLUDED.owner, updated_at=EXCLUDED.updated_at
        ''', (
            d['id'], d['url'], d['service_name'], d['owner'], d['owner_email'],
            d['env'], bool(d['enabled']), d['check_interval'], d['timeout'],
            d['agent_id'], bool(d['synced_from_agent']),
            d['created_at'], d['updated_at'],
        ))
    pg.commit()
    print(f"  ✓ targets: {len(rows)} 条")

    # 5. 迁移 credentials
    print("\n=== 迁移 credentials ===")
    sq_conn.execute("SELECT * FROM credentials")
    rows = sq_conn.fetchall()
    cols = [d[0] for d in sq_conn.description]
    for r in rows:
        d = dict(zip(cols, r))
        pg_conn.execute('''
            INSERT INTO credentials (id, name, type, type_label, expiry_date,
                owner, owner_email, env, service_name, remark,
                enabled, notify_days, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name, type=EXCLUDED.type,
                updated_at=EXCLUDED.updated_at
        ''', (
            d['id'], d['name'], d['type'], d['type_label'], d['expiry_date'],
            d['owner'], d['owner_email'], d['env'], d['service_name'], d['remark'],
            bool(d['enabled']), d['notify_days'],
            d['created_at'], d['updated_at'],
        ))
    pg.commit()
    print(f"  ✓ credentials: {len(rows)} 条")

    # 6. 迁移 agent_heartbeats
    print("\n=== 迁移 agent_heartbeats ===")
    sq_conn.execute("SELECT * FROM agent_heartbeats")
    rows = sq_conn.fetchall()
    cols = [d[0] for d in sq_conn.description]
    for r in rows:
        d = dict(zip(cols, r))
        pg_conn.execute('''
            INSERT INTO agent_heartbeats (agent_id, last_heartbeat, agent_info,
                targets_count, metrics_buffer_size, push_queue_size,
                push_mode, agent_mode, scrape_interval, local_targets, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_id) DO UPDATE SET
                last_heartbeat=EXCLUDED.last_heartbeat,
                updated_at=EXCLUDED.updated_at
        ''', (
            d['agent_id'], d['last_heartbeat'], d['agent_info'],
            d['targets_count'], d['metrics_buffer_size'], d['push_queue_size'],
            bool(d['push_mode']), d['agent_mode'], d['scrape_interval'],
            d['local_targets'], d['updated_at'],
        ))
    pg.commit()
    print(f"  ✓ agent_heartbeats: {len(rows)} 条")

    # 7. 迁移 metrics（大表，分批）
    print("\n=== 迁移 metrics ===")
    sq_conn.execute("SELECT COUNT(*) as cnt FROM metrics")
    total = sq_conn.fetchone()['cnt']
    print(f"  共 {total} 条 metrics，开始分批迁移...")

    batch_size = 500
    migrated = 0
    offset = 0
    while offset < total:
        sq_conn.execute(f"SELECT * FROM metrics ORDER BY id LIMIT {batch_size} OFFSET {offset}")
        rows = sq_conn.fetchall()
        cols = [d[0] for d in sq_conn.description]

        for r in rows:
            d = dict(zip(cols, r))
            pg_conn.execute('''
                INSERT INTO metrics (timestamp, metric_type, hostname, port,
                    service_name, owner, owner_email, env, value,
                    agent_id, agent_name, agent_hostname, source,
                    subject_cn, issuer_cn, issuer_org, subject_json, issuer_json, serial)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                d['timestamp'], d['metric_type'], d['hostname'], d['port'],
                d['service_name'], d['owner'], d['owner_email'], d['env'],
                d['value'], d['agent_id'], d['agent_name'], d['agent_hostname'],
                d['source'], d['subject_cn'], d['issuer_cn'], d['issuer_org'],
                d['subject_json'], d['issuer_json'], d['serial'],
            ))

        pg.commit()
        migrated += len(rows)
        offset += batch_size
        print(f"  进度: {migrated}/{total}")

    print(f"  ✓ metrics: {migrated} 条")

    # 验证
    print("\n=== 验证 ===")
    for table in ['agents', 'targets', 'credentials', 'metrics', 'settings', 'agent_heartbeats']:
        pg_conn.execute(f"SELECT COUNT(*) FROM {table}")
        cnt = pg_conn.fetchone()[0]
        print(f"  {table}: {cnt} 条")

    sq.close()
    pg.close()
    print("\n迁移完成！")


if __name__ == '__main__':
    migrate()

#!/usr/bin/env python3
"""
JSON → PostgreSQL (IvorySQL) 数据迁移工具
将现有 JSON 数据文件中的数据迁移到 PostgreSQL 数据库

用法:
    python migrate_json_to_db.py [--data-dir /path/to/data]
"""

import json
import os
import sys
import uuid

# 添加父目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db


def migrate(data_dir=None):
    """执行迁移"""
    if not data_dir:
        data_dir = os.getenv('DATA_DIR', '/app/data')

    print(f"数据目录: {data_dir}")
    print(f"数据库: {db.DB_HOST}:{db.DB_PORT}/{db.DB_NAME}")
    print("=" * 60)

    # 初始化数据库
    db.init_db()

    # 1. 迁移 server_config.json → agents + settings
    config_path = os.path.join(data_dir, 'server_config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agents = config.get('agents', [])
        for agent in agents:
            db.upsert_agent(agent)
        print(f"  ✓ 迁移 agents: {len(agents)} 条")

        settings = config.get('settings', {})
        for k, v in settings.items():
            db.set_setting(k, str(v))
        print(f"  ✓ 迁移 settings: {len(settings)} 条")
    else:
        print("  - 跳过 server_config.json (不存在)")

    # 2. 迁移 ssl_targets.json → targets + settings
    unified_path = os.path.join(data_dir, 'ssl_targets.json')
    if os.path.exists(unified_path):
        with open(unified_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        targets = data.get('targets', [])
        for t in targets:
            # 确保有 id
            if not t.get('id'):
                t['id'] = str(uuid.uuid4())
            db.upsert_target(t)
        print(f"  ✓ 迁移 ssl_targets: {len(targets)} 条")

        # 迁移 settings 部分
        settings = data.get('settings', {})
        for k, v in settings.items():
            if isinstance(v, (dict, list)):
                db.set_setting(k, json.dumps(v, ensure_ascii=False))
            else:
                db.set_setting(k, str(v))
        print(f"  ✓ 迁移 ssl_targets.settings: {len(settings)} 条")
    else:
        print("  - 跳过 ssl_targets.json (不存在)")

    # 3. 迁移 agent_targets.json → targets (补充 Agent 管理的目标)
    agent_targets_path = os.path.join(data_dir, 'agent_targets.json')
    if os.path.exists(agent_targets_path):
        with open(agent_targets_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        targets = data.get('targets', [])
        added = 0
        for t in targets:
            if not t.get('id'):
                t['id'] = str(uuid.uuid4())
            existing = db.get_target_by_url(t.get('url', ''), t.get('agent_id', ''))
            if not existing:
                db.upsert_target(t)
                added += 1
        print(f"  ✓ 迁移 agent_targets: {len(targets)} 条 (新增 {added})")
    else:
        print("  - 跳过 agent_targets.json (不存在)")

    # 4. 迁移 agent/data/targets.json → targets (补充)
    agent_data_path = os.path.join(os.path.dirname(data_dir), 'agent', 'data', 'targets.json')
    if not os.path.exists(agent_data_path):
        # Docker 容器内的路径
        agent_data_path = os.path.join(data_dir, '..', 'agent_data', 'targets.json')
    if os.path.exists(agent_data_path):
        with open(agent_data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        targets = data.get('targets', [])
        added = 0
        for t in targets:
            if not t.get('id'):
                t['id'] = str(uuid.uuid4())
            existing = db.get_target_by_url(t.get('url', ''), t.get('agent_id', ''))
            if not existing:
                db.upsert_target(t)
                added += 1
        print(f"  ✓ 迁移 agent/data/targets.json: {len(targets)} 条 (新增 {added})")
    else:
        print("  - 跳过 agent/data/targets.json (不存在)")

    # 5. 迁移 credentials.json → credentials
    cred_path = os.path.join(data_dir, 'credentials.json')
    if os.path.exists(cred_path):
        with open(cred_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        credentials = data.get('credentials', [])
        for c in credentials:
            if not c.get('id'):
                c['id'] = str(uuid.uuid4())
            # 移除计算字段（不存入 DB，查询时动态计算）
            c.pop('status', None)
            c.pop('days_left', None)
            c.pop('level', None)
            db.upsert_credential(c)
        print(f"  ✓ 迁移 credentials: {len(credentials)} 条")
    else:
        print("  - 跳过 credentials.json (不存在)")

    # 6. 迁移 metrics.json → metrics (大文件，分批处理)
    metrics_path = os.path.join(data_dir, 'metrics.json')
    if os.path.exists(metrics_path):
        print(f"  ⏳ 迁移 metrics.json (可能较大)...")
        batch_size = 500
        total = 0
        with open(metrics_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        metrics = data.get('metrics', [])
        for i in range(0, len(metrics), batch_size):
            batch = metrics[i:i + batch_size]
            db.insert_metrics(batch)
            total += len(batch)
            print(f"    进度: {min(i + batch_size, len(metrics))}/{len(metrics)}")

        print(f"  ✓ 迁移 metrics: {total} 条")
    else:
        print("  - 跳过 metrics.json (不存在)")

    print("=" * 60)
    print("迁移完成!")

    # 验证
    print("\n验证结果:")
    with db.get_db() as conn:
        for table in ['agents', 'targets', 'credentials', 'metrics', 'settings', 'agent_heartbeats']:
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                cnt = cur.fetchone()[0]
                cur.close()
                print(f"  {table}: {cnt} 条记录")
            except Exception:
                print(f"  {table}: 表不存在")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='JSON → PostgreSQL (IvorySQL) 迁移工具')
    parser.add_argument('--data-dir', default=None, help='数据目录路径')
    args = parser.parse_args()
    migrate(args.data_dir)

"""扫描 db/migrations/*.sql 按文件名顺序执行,记录于 schema_migrations 表,严格幂等。

用 DATABASE_URL_ADMIN(spider superuser)而不是 DATABASE_URL(spider_app),因为 migrations
需要 SUPERUSER 权限来 CREATE ROLE / ALTER TABLE / FORCE RLS 等 DDL。

跑 migrations 前 SET LOCAL app.app_password,migration 005 用此变量建 spider_app role。

用法:
    python -m db.migration_runner
"""
import os
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def main() -> None:
    dsn = os.environ.get("DATABASE_URL_ADMIN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL_ADMIN / DATABASE_URL 都未设置;查 ~/.spider_xhs/.env")
    app_password = os.environ.get("SPIDER_APP_PASSWORD", "")
    if not app_password:
        print("[warn] SPIDER_APP_PASSWORD 未设置;migration 005 会失败")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        conn.commit()
        cur.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = sql_file.stem
            if version in applied:
                print(f"[skip ] {version}")
                continue
            print(f"[apply] {version}")
            # SET LOCAL 必须先于业务 SQL,在同一事务里
            if app_password:
                cur.execute("SET LOCAL app.app_password = %s", (app_password,))
            cur.execute(sql_file.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (version,))
            conn.commit()
    conn.close()
    print("migrations complete")


if __name__ == "__main__":
    main()

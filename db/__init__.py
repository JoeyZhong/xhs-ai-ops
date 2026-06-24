"""
Phase 4a · PostgreSQL 持久化层。

外部入口:
    from db.session import init_pool, get_rls_cursor, close_pool

不要直连 psycopg2.connect 绕过本模块——会跳过 RLS 上下文。
"""

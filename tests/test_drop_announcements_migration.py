"""공지 기능을 없앤 migration: announcements 테이블만 지우고 되돌리면 같은 모양으로 다시 만든다."""
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_drop_announcements_upgrade_and_downgrade(monkeypatch, tmp_path):
    migration = importlib.import_module("migrations.versions.b8c9d0e1f2a3_drop_announcements")
    engine = create_engine(f"sqlite:///{tmp_path}/migration.db")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users VALUES (1)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()  # 공지 테이블이 있던 상태를 만든다
        assert "announcements" in inspect(connection).get_table_names()
        connection.execute(text("INSERT INTO announcements (user_id, lms_course_id, course_name, lms_id, title, created_at) "
                                "VALUES (1, 1, 'c', 1, 't', '2026-11-10')"))
        migration.upgrade()
        tables = inspect(connection).get_table_names()
        assert "announcements" not in tables and "users" in tables
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    engine.dispose()

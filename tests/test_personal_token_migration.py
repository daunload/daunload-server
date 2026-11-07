"""Exercise the additive migration against an existing credentials row."""
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_token_migration_keeps_old_sessions_and_defaults(monkeypatch, tmp_path):
    migration = importlib.import_module('migrations.versions.f6b7c8d9e0a1_canvas_personal_token')
    engine = create_engine(f'sqlite:///{tmp_path}/migration.db')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE lms_credentials (user_id INTEGER PRIMARY KEY, session_enc TEXT)'))
        connection.execute(text("INSERT INTO lms_credentials VALUES (1, 'existing-encrypted-session')"))
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        row = connection.execute(text('SELECT * FROM lms_credentials')).mappings().one()
        assert row['session_enc'] == 'existing-encrypted-session'
        assert row['personal_token_enc'] is None
        assert row['personal_token_status'] == 'none'
        assert row['personal_token_registered_at'] is None
        assert row['credential_version'] == 0
        migration.downgrade()
        assert {c['name'] for c in inspect(connection).get_columns('lms_credentials')} == {'user_id', 'session_enc'}
        assert connection.execute(text('SELECT session_enc FROM lms_credentials')).scalar_one() == 'existing-encrypted-session'
    engine.dispose()

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def repair():
    path = (
        Path(__file__).resolve().parents[3]
        / "migrations/versions/2026_11_15_0330-bbc15a7e0003_repair_skipped_workflow_schema.py"
    )
    spec = importlib.util.spec_from_file_location("skipped_workflow_schema_repair", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing_version_column", [False, True])
def test_repair_preserves_rows_and_can_run_again(repair, existing_version_column):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for table in (
            "agent_config_drafts",
            "agent_config_snapshots",
            "conversations",
            "workflow_node_executions",
            "agent_runtime_sessions",
        ):
            connection.exec_driver_sql(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, payload TEXT)")
            connection.exec_driver_sql(f"INSERT INTO {table} VALUES ('existing', 'preserve')")
        connection.exec_driver_sql("CREATE TABLE workflows (id TEXT PRIMARY KEY, app_id TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO workflows VALUES ('existing', 'app-1')")
        if existing_version_column:
            connection.exec_driver_sql("ALTER TABLE workflows ADD COLUMN version_number INTEGER")
            connection.exec_driver_sql("UPDATE workflows SET version_number = 7")
        repair.op = Operations(MigrationContext.configure(connection))

        repair.upgrade()
        repair.upgrade()
        repair.downgrade()

        inspector = sa.inspect(connection)
        assert inspector.has_table("agent_home_snapshots")
        assert inspector.has_table("agent_workspaces")
        assert inspector.has_table("agent_workspace_bindings")
        assert inspector.has_table("workflow_version_counters")
        assert not inspector.has_table("streaming_tts_configs")
        for table in (
            "agent_config_drafts",
            "agent_config_snapshots",
            "conversations",
            "workflow_node_executions",
            "agent_runtime_sessions",
        ):
            assert connection.exec_driver_sql(f"SELECT payload FROM {table}").scalar_one() == "preserve"
        for table, column in (
            ("agent_config_drafts", "home_snapshot_id"),
            ("agent_config_drafts", "agent_workspace_binding_id"),
            ("agent_config_snapshots", "home_snapshot_id"),
            ("conversations", "agent_workspace_binding_id"),
            ("workflow_node_executions", "agent_workspace_binding_id"),
            ("workflows", "version_number"),
        ):
            columns = {item["name"]: item for item in inspector.get_columns(table)}
            assert columns[column]["nullable"]
        version = connection.exec_driver_sql("SELECT version_number FROM workflows").scalar_one()
        counter = connection.exec_driver_sql("SELECT last_version_number FROM workflow_version_counters").scalar()
        assert version == (7 if existing_version_column else None)
        assert counter == (7 if existing_version_column else None)
        indexes = {item["name"]: item for item in inspector.get_indexes("workflows")}
        assert indexes["workflow_app_version_number_idx"]["unique"]

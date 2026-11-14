"""Repair schema skipped by the historical custom TTS migration lineage.

Revision ID: bbc15a7e0003
Revises: bbc15a7e0002

Some deployments recorded bbc14a7e0001 before its parent chain included the
upstream home/workspace and workflow-number migrations. Alembic therefore sees
those ancestors as applied even though their DDL is absent. Converge additively
on their final schema, preserving business rows and legacy runtime sessions.
"""

import sqlalchemy as sa
from alembic import op

import models

revision = "bbc15a7e0003"
down_revision = "bbc15a7e0002"
branch_labels = None
depends_on = None


def _has_table(name):
    return sa.inspect(op.get_bind()).has_table(name)


def _add_nullable_column(table, column):
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def _has_index(table, name):
    return name in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    if not _has_table("agent_home_snapshots"):
        op.create_table(
            "agent_home_snapshots",
            sa.Column("id", models.types.StringUUID(), nullable=False),
            sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
            sa.Column("agent_id", models.types.StringUUID(), nullable=False),
            sa.Column("snapshot_ref", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id", name="agent_home_snapshot_pkey"),
        )

    if not _has_table("agent_workspace_bindings"):
        op.create_table(
            "agent_workspace_bindings",
            sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
            sa.Column("app_id", models.types.StringUUID(), nullable=False),
            sa.Column("workspace_id", models.types.StringUUID(), nullable=False),
            sa.Column("agent_id", models.types.StringUUID(), nullable=False),
            sa.Column("base_home_snapshot_id", models.types.StringUUID(), nullable=True),
            sa.Column("agent_config_version_id", models.types.StringUUID(), nullable=False),
            sa.Column("agent_config_version_kind", sa.String(length=32), nullable=False),
            sa.Column("backend_binding_ref", sa.String(length=255), nullable=False),
            sa.Column("session_snapshot", models.types.LongText(), nullable=True),
            sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("pending_form_id", models.types.StringUUID(), nullable=True),
            sa.Column("pending_tool_call_id", sa.String(length=255), nullable=True),
            sa.Column("id", models.types.StringUUID(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id", name="agent_workspace_binding_pkey"),
        )

    if not _has_table("agent_workspaces"):
        op.create_table(
            "agent_workspaces",
            sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
            sa.Column("app_id", models.types.StringUUID(), nullable=False),
            sa.Column("owner_type", sa.String(length=32), nullable=False),
            sa.Column("owner_id", models.types.StringUUID(), nullable=False),
            sa.Column("owner_scope_key", sa.String(length=255), nullable=False),
            sa.Column("backend_workspace_ref", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
            sa.Column("active_guard", sa.SmallInteger(), server_default="1", nullable=True),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("id", models.types.StringUUID(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id", name="agent_workspace_pkey"),
        )

    if not _has_table("workflow_version_counters"):
        op.create_table(
            "workflow_version_counters",
            sa.Column("app_id", models.types.StringUUID(), nullable=False),
            sa.Column("last_version_number", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("app_id", name="workflow_version_counter_pkey"),
        )

        counters_created = True
    else:
        counters_created = False

    _add_nullable_column("agent_config_drafts", sa.Column("home_snapshot_id", models.types.StringUUID(), nullable=True))

    _add_nullable_column(
        "agent_config_snapshots", sa.Column("home_snapshot_id", models.types.StringUUID(), nullable=True)
    )

    _add_nullable_column(
        "conversations", sa.Column("agent_workspace_binding_id", models.types.StringUUID(), nullable=True)
    )

    _add_nullable_column(
        "agent_config_drafts", sa.Column("agent_workspace_binding_id", models.types.StringUUID(), nullable=True)
    )

    _add_nullable_column(
        "workflow_node_executions", sa.Column("agent_workspace_binding_id", models.types.StringUUID(), nullable=True)
    )

    _add_nullable_column("workflows", sa.Column("version_number", sa.Integer(), nullable=True))

    if counters_created:
        # Preserve monotonic numbering if only the counter table was missing.
        op.execute(
            sa.text(
                "INSERT INTO workflow_version_counters (app_id, last_version_number) "
                "SELECT app_id, MAX(version_number) FROM workflows "
                "WHERE version_number IS NOT NULL GROUP BY app_id"
            )
        )

    if not _has_index("agent_home_snapshots", "agent_home_snapshot_tenant_agent_idx"):
        op.create_index(
            "agent_home_snapshot_tenant_agent_idx", "agent_home_snapshots", ["tenant_id", "agent_id"], unique=False
        )

    if not _has_index("agent_workspace_bindings", "agent_workspace_binding_agent_status_idx"):
        op.create_index(
            "agent_workspace_binding_agent_status_idx",
            "agent_workspace_bindings",
            ["tenant_id", "agent_id", "status"],
            unique=False,
        )

    if not _has_index("agent_workspace_bindings", "agent_workspace_binding_status_retired_idx"):
        op.create_index(
            "agent_workspace_binding_status_retired_idx",
            "agent_workspace_bindings",
            ["status", "retired_at"],
            unique=False,
        )

    if not _has_index("agent_workspace_bindings", "agent_workspace_binding_workspace_status_idx"):
        op.create_index(
            "agent_workspace_binding_workspace_status_idx",
            "agent_workspace_bindings",
            ["tenant_id", "workspace_id", "status"],
            unique=False,
        )

    if not _has_index("agent_workspaces", "agent_workspace_owner_active_unique"):
        op.create_index(
            "agent_workspace_owner_active_unique",
            "agent_workspaces",
            ["tenant_id", "owner_type", "owner_id", "owner_scope_key", "active_guard"],
            unique=True,
        )

    if not _has_index("agent_workspaces", "agent_workspace_status_retired_idx"):
        op.create_index(
            "agent_workspace_status_retired_idx", "agent_workspaces", ["status", "retired_at"], unique=False
        )

    if not _has_index("agent_workspaces", "agent_workspace_tenant_app_status_idx"):
        op.create_index(
            "agent_workspace_tenant_app_status_idx", "agent_workspaces", ["tenant_id", "app_id", "status"], unique=False
        )

    if not _has_index("agent_workspaces", "agent_workspace_tenant_status_idx"):
        op.create_index("agent_workspace_tenant_status_idx", "agent_workspaces", ["tenant_id", "status"], unique=False)

    if not _has_index("agent_home_snapshots", "agent_home_snapshot_status_retired_idx"):
        op.create_index(
            "agent_home_snapshot_status_retired_idx", "agent_home_snapshots", ["status", "retired_at"], unique=False
        )

    if not _has_index("workflows", "workflow_app_version_number_idx"):
        op.create_index(
            "workflow_app_version_number_idx",
            "workflows",
            ["app_id", "version_number"],
            unique=True,
            postgresql_where=sa.text("version_number IS NOT NULL"),
        )


def downgrade():
    # These structures belong to ancestor migrations, not this repair revision.
    # Removing them would break the schema expected by our down_revision.
    pass

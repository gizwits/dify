"""Add workspace streaming TTS configuration.

Revision ID: bbc14a7e0001
Revises: a1c7f4e9b3d2
"""

import sqlalchemy as sa
from alembic import op

from models.types import LongText, StringUUID

revision = "bbc14a7e0001"
down_revision = "a1c7f4e9b3d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "streaming_tts_configs",
        sa.Column("tenant_id", StringUUID(), primary_key=True),
        sa.Column("settings", LongText(), nullable=False),
        sa.Column("encrypted_api_key", LongText(), nullable=False),
    )


def downgrade():
    op.drop_table("streaming_tts_configs")

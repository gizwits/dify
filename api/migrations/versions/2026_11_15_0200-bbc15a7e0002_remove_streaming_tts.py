"""Remove the retired workspace streaming TTS configuration.

Revision ID: bbc15a7e0002
Revises: bbc14a7e0001
"""

import sqlalchemy as sa
from alembic import op

from models.types import LongText, StringUUID

revision = "bbc15a7e0002"
down_revision = "bbc14a7e0001"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("streaming_tts_configs")


def downgrade():
    # Restore the old schema only; retired credentials require a separate backup.
    op.create_table(
        "streaming_tts_configs",
        sa.Column("tenant_id", StringUUID(), primary_key=True),
        sa.Column("settings", LongText(), nullable=False),
        sa.Column("encrypted_api_key", LongText(), nullable=False),
    )

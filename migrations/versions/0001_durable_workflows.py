"""Durable jobs, source generations and local vector reference index.

Revision definitions import a frozen versioned schema, never future live models.
"""

from alembic import context, op

from incidentops.durable.schema_v1 import metadata

revision = "0001_durable_workflows"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(bind=op.get_bind(), checkfirst=not context.is_offline_mode())


def downgrade():
    metadata.drop_all(bind=op.get_bind(), checkfirst=not context.is_offline_mode())

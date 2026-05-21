# migrations/versions/006_widget_description.py
"""Add `description` column to widget_configs.

The demo host now shows end users a chooser of active widgets. Admins write
a short description so users can pick the widget that suits them ("This one
is best for terraform docs questions", etc.). enabled_tools is shown as
chips alongside the description.
"""
from alembic import op


revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE widget_configs ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    op.execute(
        "UPDATE widget_configs SET description = "
        "'Default demo widget — answers Terraform questions with retrieval + summarization.' "
        "WHERE widget_id = 'demo' AND description = ''"
    )


def downgrade():
    op.execute("ALTER TABLE widget_configs DROP COLUMN description")

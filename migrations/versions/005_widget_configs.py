# migrations/versions/005_widget_configs.py
"""Add widget_configs table and seed a default 'demo' widget.

Each widget config holds:
  - widget_id      : caller-chosen string used in <script data-widget-id="...">
  - name           : display label for the admin UI
  - allowed_origins: list of origins permitted to embed the widget iframe;
                     used to build CSP frame-ancestors on /widget/{id}/embed.
                     `['*']` allows any framer (use sparingly).
  - theme          : JSONB with color, position ('bottom-right'/'bottom-left'),
                     and greeting text shown as the first assistant bubble.
  - enabled_tools  : list of tool names the LLM is allowed to call for this
                     widget session (subset of the chatbot's TOOLS).
  - is_active      : soft delete flag; inactive widgets refuse new sessions.
"""
from alembic import op


revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE widget_configs (
            widget_id        TEXT PRIMARY KEY,
            name             TEXT NOT NULL DEFAULT '',
            allowed_origins  TEXT[] NOT NULL DEFAULT '{}',
            theme            JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled_tools    TEXT[] NOT NULL DEFAULT '{}',
            is_active        BOOLEAN NOT NULL DEFAULT true,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Seed the demo widget so demo/host/index.html works out of the box.
    op.execute(
        """
        INSERT INTO widget_configs (widget_id, name, allowed_origins, theme, enabled_tools)
        VALUES (
            'demo',
            'Demo Widget',
            ARRAY['*'],
            '{"color": "#4f46e5", "position": "bottom-right", "greeting": "Hi! Ask me anything about Terraform."}'::jsonb,
            ARRAY['search_knowledge', 'summarize_thread', 'extract_entities']
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS widget_configs")

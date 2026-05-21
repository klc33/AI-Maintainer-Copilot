# streamlit_app/pages/3_Admin_Widgets.py
"""Admin CRUD for widget configs.

Talks to /admin/widgets/* via the user's JWT. Lists existing widgets, lets
the admin create / edit / delete, and shows the embed snippet that hosts
should paste."""
import streamlit as st
import requests
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _auth import sidebar_logout, require_admin

API_URL = "http://api:8000"

# Public host base for the embed snippet shown to admins. The actual widget
# bundle is served by the widget nginx container on port 8080.
PUBLIC_WIDGET_HOST = os.environ.get("PUBLIC_WIDGET_HOST", "http://localhost:8080")

# Tool names that match the chatbot's TOOLS list.
AVAILABLE_TOOLS = [
    "search_knowledge",
    "summarize_thread",
    "extract_entities",
    "classify_issue",
    "write_memory",
]

if not st.session_state.get("token"):
    st.warning("Please log in.")
    st.stop()

require_admin()
sidebar_logout()


def _headers():
    return {"Authorization": f"Bearer {st.session_state['token']}"}


def _list_widgets():
    r = requests.get(f"{API_URL}/admin/widgets", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()["widgets"]


def _create_widget(payload):
    return requests.post(f"{API_URL}/admin/widgets", headers=_headers(), json=payload, timeout=10)


def _update_widget(widget_id, payload):
    return requests.patch(f"{API_URL}/admin/widgets/{widget_id}", headers=_headers(), json=payload, timeout=10)


def _delete_widget(widget_id):
    return requests.delete(f"{API_URL}/admin/widgets/{widget_id}", headers=_headers(), timeout=10)


def _embed_snippet(widget_id: str) -> str:
    return (
        f'<script src="{PUBLIC_WIDGET_HOST}/widget.js" '
        f'data-widget-id="{widget_id}"></script>'
    )


st.title("Admin · Widget Configurations")

try:
    widgets = _list_widgets()
except Exception as e:
    st.error(f"Could not load widget configs: {e}")
    st.stop()


# ── List existing widgets ─────────────────────────────
st.subheader("Configured widgets")
if not widgets:
    st.info("No widgets configured yet. Create one below.")
for cfg in widgets:
    wid = cfg["widget_id"]
    with st.expander(f"**{cfg['name'] or wid}** ({wid}) — {'active' if cfg['is_active'] else 'inactive'}"):
        with st.form(f"edit_{wid}", clear_on_submit=False):
            name = st.text_input("Display name", cfg["name"], key=f"name_{wid}")
            description = st.text_area(
                "Description (shown to users in the demo chooser)",
                cfg.get("description", ""),
                key=f"desc_{wid}",
                help="A short explanation of what this widget does or who it's for.",
                height=80,
            )
            allowed = st.text_area(
                "Allowed origins (comma-separated; `*` allows any framer)",
                ", ".join(cfg["allowed_origins"]) if cfg["allowed_origins"] else "",
                key=f"origins_{wid}",
                help="Sets the embed page's Content-Security-Policy frame-ancestors directive.",
            )
            theme = cfg.get("theme") or {}
            c1, c2 = st.columns(2)
            color = c1.color_picker("Theme color", theme.get("color", "#4f46e5"), key=f"color_{wid}")
            position = c2.selectbox(
                "Bubble position",
                ["bottom-right", "bottom-left"],
                index=0 if theme.get("position", "bottom-right") == "bottom-right" else 1,
                key=f"pos_{wid}",
            )
            greeting = st.text_area(
                "Greeting (shown as the first assistant bubble)",
                theme.get("greeting", ""),
                key=f"greet_{wid}",
            )
            enabled = st.multiselect(
                "Enabled tools",
                AVAILABLE_TOOLS,
                default=cfg["enabled_tools"] or [],
                key=f"tools_{wid}",
            )
            active = st.checkbox("Active", value=cfg["is_active"], key=f"active_{wid}")

            save = st.form_submit_button("Save changes")
            if save:
                payload = {
                    "name": name.strip(),
                    "description": description.strip(),
                    "allowed_origins": [o.strip() for o in allowed.split(",") if o.strip()],
                    "theme": {"color": color, "position": position, "greeting": greeting},
                    "enabled_tools": enabled,
                    "is_active": active,
                }
                r = _update_widget(wid, payload)
                if r.status_code == 200:
                    st.success("Saved.")
                    st.rerun()
                else:
                    st.error(f"Update failed ({r.status_code}): {r.text}")

        st.code(_embed_snippet(wid), language="html")
        st.caption("Paste this into the host page to embed.")
        if st.button("🗑️ Delete this widget", key=f"del_{wid}"):
            r = _delete_widget(wid)
            if r.status_code in (200, 204):
                st.success(f"Deleted {wid}.")
                st.rerun()
            else:
                st.error(f"Delete failed ({r.status_code}): {r.text}")


# ── Create a new widget ───────────────────────────────
st.divider()
st.subheader("Create a new widget")
with st.form("new_widget"):
    new_id = st.text_input("Widget ID", help="Used in <script data-widget-id=\"...\">. Must be unique.")
    new_name = st.text_input("Display name")
    new_desc = st.text_area(
        "Description",
        "",
        help="A short paragraph explaining what this widget does and when to pick it.",
        height=80,
    )
    new_allowed = st.text_area(
        "Allowed origins (comma-separated)",
        "*",
        help="Use `*` for any framer during dev, or list specific origins like `https://example.com`.",
    )
    c1, c2 = st.columns(2)
    new_color = c1.color_picker("Theme color", "#4f46e5")
    new_pos = c2.selectbox("Bubble position", ["bottom-right", "bottom-left"])
    new_greet = st.text_area("Greeting", "Hi! How can I help?")
    new_tools = st.multiselect(
        "Enabled tools",
        AVAILABLE_TOOLS,
        default=["search_knowledge", "summarize_thread"],
    )
    create = st.form_submit_button("Create widget")
    if create:
        if not new_id.strip():
            st.error("Widget ID is required.")
        else:
            payload = {
                "widget_id": new_id.strip(),
                "name": new_name.strip(),
                "description": new_desc.strip(),
                "allowed_origins": [o.strip() for o in new_allowed.split(",") if o.strip()],
                "theme": {"color": new_color, "position": new_pos, "greeting": new_greet},
                "enabled_tools": new_tools,
                "is_active": True,
            }
            r = _create_widget(payload)
            if r.status_code == 201:
                st.success(f"Created widget '{new_id}'.")
                st.rerun()
            else:
                st.error(f"Create failed ({r.status_code}): {r.text}")

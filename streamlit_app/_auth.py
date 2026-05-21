# streamlit_app/_auth.py
"""Shared auth helpers for the Streamlit admin UI.

Streamlit is treated as an internal admin/maintainer tool here. End users
talk to the chatbot via the embeddable widget instead, so we restrict
Streamlit access to users with role='admin'. Anyone else who somehow has a
valid API token still gets bounced at the page boundary.
"""
import streamlit as st


def sidebar_logout() -> None:
    """Render the logged-in user's info and a logout button in the sidebar.
    No-op if no token is in session state."""
    if not st.session_state.get("token"):
        return
    user = st.session_state.get("user") or {}
    with st.sidebar:
        st.markdown(f"**{user.get('email', 'user')}**")
        st.caption(f"Role: {user.get('role', 'user')}")
        st.divider()
        if st.button("🚪 Log out", use_container_width=True, key="_sidebar_logout"):
            for k in ("token", "user"):
                st.session_state.pop(k, None)
            st.rerun()


def require_admin() -> None:
    """Boot any non-admin out of the page. Call near the top of every page
    after the usual `if not token: st.warning(); st.stop()` guard."""
    user = st.session_state.get("user")
    if not user:
        return  # the per-page "please log in" guard will handle this
    if user.get("role") != "admin":
        for k in ("token", "user"):
            st.session_state.pop(k, None)
        st.error("Streamlit access is restricted to admin users. Logged out.")
        st.stop()

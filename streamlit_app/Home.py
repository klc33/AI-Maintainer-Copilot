# streamlit_app/Home.py
import streamlit as st
import requests
from _auth import sidebar_logout, require_admin

st.set_page_config(page_title="Maintainer's Copilot", layout="wide")

API_URL = "http://api:8000"

# Initialize session state
if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.user = None

# Bounce any non-admin token left over in session state.
require_admin()

# Render the sidebar logout block whenever a token is present.
sidebar_logout()

# ── Authenticated admin view ─────────────────────────
if st.session_state.token and isinstance(st.session_state.user, dict):
    email = st.session_state.user.get("email", "user")
    st.success(f"Logged in as {email}")

    st.page_link("pages/1_Chat.py", label="💬 Go to Chat")
    st.page_link("pages/2_Memory_Inspector.py", label="🧠 Memory Inspector")
    st.page_link("pages/3_Admin_Widgets.py", label="⚙️ Admin: Widget Configs")

# ── Login form ────────────────────────────────────────
else:
    st.title("Maintainer's Copilot – Admin login")
    st.caption("This UI is restricted to admin users. End users go through the embeddable widget.")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Log in"):
        # 1. Get JWT token
        resp = requests.post(
            f"{API_URL}/auth/jwt/login",
            data={"username": email, "password": password},
        )
        if resp.status_code != 200:
            st.error("Invalid email or password.")
        else:
            token = resp.json()["access_token"]

            # 2. Fetch current user info
            user_resp = requests.get(
                f"{API_URL}/auth/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if user_resp.status_code != 200:
                st.error("Logged in but could not fetch user details. Please try again.")
            else:
                user = user_resp.json()
                if user.get("role") != "admin":
                    # Never store the token for non-admins — they get bounced
                    # before they can browse any protected page.
                    st.error("This UI is for admins only. Use the widget to chat.")
                else:
                    st.session_state.token = token
                    st.session_state.user = user
                    st.rerun()

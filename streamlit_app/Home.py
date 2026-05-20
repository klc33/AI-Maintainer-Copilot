# streamlit_app/Home.py
import streamlit as st
import requests

st.set_page_config(page_title="Maintainer's Copilot", layout="wide")

API_URL = "http://api:8000"

# Initialize session state
if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.user = None

# ── Authenticated users ───────────────────────────────
if st.session_state.token and isinstance(st.session_state.user, dict):
    email = st.session_state.user.get("email", "user")
    st.success(f"Logged in as {email}")

    st.page_link("pages/1_Chat.py", label="💬 Go to Chat")
    st.page_link("pages/2_Memory_Inspector.py", label="🧠 Memory Inspector")

    if st.session_state.user.get("role") == "admin":
        st.page_link("pages/3_Admin_Widgets.py", label="⚙️ Admin: Widget Configs")

# ── Login form ────────────────────────────────────────
else:
    st.title("Maintainer's Copilot – Login")
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
            st.session_state.token = token

            # 2. Fetch current user info
            user_resp = requests.get(
                f"{API_URL}/auth/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if user_resp.status_code == 200:
                st.session_state.user = user_resp.json()
                st.rerun()
            else:
                # Could not get user – revoke token
                st.session_state.token = None
                st.error("Logged in but could not fetch user details. Please try again.")
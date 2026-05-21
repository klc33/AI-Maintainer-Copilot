import streamlit as st
import requests
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _auth import sidebar_logout, require_admin

API_URL = "http://api:8000"

st.title("Memory Inspector")
if not st.session_state.get("token"):
    st.warning("Please log in.")
    st.stop()

require_admin()
sidebar_logout()

resp = requests.get(
    f"{API_URL}/memory/list",
    headers={"Authorization": f"Bearer {st.session_state.token}"},
)
if resp.status_code == 200:
    memories = resp.json()["memories"]
    if not memories:
        st.info("No memories stored yet.")
    else:
        for mem in memories:
            with st.expander(mem["summary"][:80] + "..."):
                st.write("**Summary:**", mem["summary"])
                if mem.get("entities"):
                    st.write("**Entities:**", mem["entities"])
                st.caption(f"Created: {mem['created_at']}")
else:
    st.error("Failed to load memories")
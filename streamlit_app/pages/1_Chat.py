import streamlit as st
import requests
import json
import httpx
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _auth import sidebar_logout, require_admin

API_URL = "http://api:8000"

if not st.session_state.get("token"):
    st.warning("Please log in first.")
    st.stop()

require_admin()
sidebar_logout()

st.title("Chat")

# Initialize conversation ID
if "conv_id" not in st.session_state:
    st.session_state.conv_id = "streamlit_session"

user_msg = st.chat_input("Your message")
if user_msg:
    with st.chat_message("user"):
        st.write(user_msg)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_text = ""

        # Stream from API
        with httpx.stream(
            "POST",
            f"{API_URL}/chat/message",
            json={"message": user_msg, "conversation_id": st.session_state.conv_id},
            headers={"Authorization": f"Bearer {st.session_state.token}"},
            timeout=30.0,
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if data["type"] == "token":
                        full_text += data["content"]
                        placeholder.markdown(full_text + "▌")
                    elif data["type"] == "tool_call_start":
                        placeholder.markdown(f"🔧 Using {data['name']}...")
                    elif data["type"] == "tool_call_result":
                        pass  # we could show results, but keep UI clean
                    elif data["type"] == "done":
                        pass
            placeholder.markdown(full_text)
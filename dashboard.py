"""Streamlit dashboard for the 3D Printer Production Simulator."""

import streamlit as st

st.set_page_config(
    page_title="3D Printer Production Simulator",
    layout="wide",
)

st.title("3D Printer Production Simulator")

with st.sidebar:
    st.header("Controls")
    st.write("Simulation controls will appear here.")

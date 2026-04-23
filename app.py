import streamlit as st

st.title("Hello World! 🚀")
st.write("This is my first Streamlit app, now live on WordPress.")

# Add a little interactivity just for fun
name = st.text_input("What is your name?")
if name:
    st.write(f"Welcome to the team, {name}!")

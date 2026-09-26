# Regular render-function modules (home.py, programme.py, ...), NOT Streamlit's
# native multipage auto-discovery -- app.py drives one persistent custom
# sidebar and calls render_xxx() from here, so navigation stays exactly as
# specified rather than however Streamlit's own page switcher looks.

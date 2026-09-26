@echo off
cd /d "%~dp0"
echo Starting LensaRak Enhanced Platform...
echo (First login: admin / lensarak123)
python -m streamlit run app.py

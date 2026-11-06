# Sobe o painel de treino. Uso: .\start.ps1   (abre em http://localhost:8501)
Set-Location -Path $PSScriptRoot
python -m streamlit run app/dashboard.py @args

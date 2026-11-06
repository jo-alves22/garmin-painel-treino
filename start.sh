#!/usr/bin/env bash
# Sobe o painel de treino. Uso: ./start.sh   (abre em http://localhost:8501)
cd "$(dirname "$0")" || exit 1
exec python3 -m streamlit run app/dashboard.py "$@"

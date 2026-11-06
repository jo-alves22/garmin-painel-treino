"""Importa o plano real a partir do xlsx (aba Calendário) para plan_sessions.

Uso (CLI, pasta local):
    python -m app.import_plan ~/Documents/Treino/plano_treino_5k_sub20_v3.xlsx [ano]

Ou, pelo próprio painel (aba Plano), suba o .xlsx direto — ver `import_plan`,
que aceita tanto um caminho quanto o arquivo enviado pelo `st.file_uploader`.

Lê a aba "📆 Calendário" (linhas no formato  'DD/MM' | 'Dia' | 'texto da sessão'),
classifica cada dia em Easy / Qualidade / Longo / Forca, e cria as sessões.
Dias que combinam corrida + força (ex.: "⚡ Limiar ... + 💪 Força A") viram DUAS
sessões (uma de corrida, uma Forca) para casar com as duas atividades do Garmin.
Dias de descanso são ignorados (não são "treino a fazer").

A planilha só tem 'DD/MM', sem ano — por isso `year` é obrigatório (a aba não dá
pra inferir isso sozinha, e errar o ano quebra o casamento com as atividades).

Substitui o conteúdo atual de plan_sessions (bootstrap do plano no app).
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import BinaryIO

import openpyxl

from . import db, plan

CAL_SHEET = "📆 Calendário"


def _km(text: str) -> float | None:
    m = re.search(r"(\d+)(?:\s*[–-]\s*\d+)?\s*km", text)
    return float(m.group(1)) if m else None


def _classify(text: str) -> tuple[str | None, str, float | None]:
    """Retorna (kind_principal, titulo, km_alvo) da sessão de corrida/qualidade.
    kind_principal = None em dias de descanso."""
    t = text
    if "TESTE 5K" in t:
        return "Qualidade", "Teste 5K", 5.0
    # "LONGÃO" (maiúsculo) é o marcador da sessão; "não é longão ainda" (minúsculo) não conta.
    if "LONGÃO" in t:
        km = _km(t)
        return "Longo", f"Longão {int(km)} km" if km else "Longão", km
    if re.search(r"Limiar|VO2|Afiar|ritmo de prova", t, re.I):
        label = "Limiar" if re.search(r"Limiar", t, re.I) else "VO2max"
        return "Qualidade", label, None  # intervalos: não extrai km (evita match ruim)
    if "DESCANSO" in t.upper():
        return None, "Descanso", None
    if re.search(r"\bEasy\b", t, re.I):
        km = _km(t)
        return "Easy", f"Easy {int(km)} km" if km else "Easy", km
    return "Easy", "Treino", _km(t)  # fallback conservador


def _forca_title(text: str) -> str | None:
    if "Força A" in text or "Forca A" in text:
        return "Força A — pernas"
    if "Força B" in text or "Forca B" in text:
        return "Força B — potência/plio"
    if "Força" in text or "Forca" in text:
        return "Força"
    return None


def parse_calendar(xlsx_source: str | Path | BinaryIO, year: int) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_source, data_only=True)
    ws = wb[CAL_SHEET]
    sessions: list[dict] = []
    for row in ws.iter_rows(values_only=True):
        c0 = str(row[0]).strip() if row and row[0] else ""
        if not re.match(r"^\d{2}/\d{2}$", c0):
            continue
        day, month = (int(x) for x in c0.split("/"))
        date = dt.date(year, month, day).isoformat()
        text = str(row[2]).strip() if len(row) > 2 and row[2] else ""

        kind, title, km = _classify(text)
        if kind is not None:  # pula descanso
            sessions.append({"date": date, "kind": kind, "title": title,
                             "target": text, "target_km": km})
        # Dia combinado com força -> sessão extra
        ft = _forca_title(text)
        if ft:
            sessions.append({"date": date, "kind": "Forca", "title": ft,
                             "target": text, "target_km": None})
    return sessions


def import_plan(xlsx_source: str | Path | BinaryIO, year: int, replace: bool = True) -> int:
    db.init_db()
    if isinstance(xlsx_source, (str, Path)):
        xlsx_source = Path(xlsx_source).expanduser()
    sessions = parse_calendar(xlsx_source, year)
    with db.connect() as conn:
        if replace:
            conn.execute("DELETE FROM plan_sessions")
    for s in sessions:
        plan.add_session(s["date"], s["kind"], s["title"], s["target"], s["target_km"])
    return len(sessions)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else \
        str(Path.home() / "Documents/Treino/plano_treino_5k_sub20_v3.xlsx")
    year = int(sys.argv[2]) if len(sys.argv) > 2 else dt.date.today().year
    n = import_plan(path, year)
    print(f"{n} sessões importadas de {path} (ano {year})")

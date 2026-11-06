"""Importa o VO2max oficial (relógio/Firstbeat) do export GDPR da Garmin.

A Personal API não expõe `vo2MaxValue` (só rótulos como "IMPROVING_VO2_MAX_15" e o
`aerobicTrainingEffect` 0–5). O número de verdade só vem no export completo da conta
("Exportar seus dados" em account.garmin.com), dentro de:

    DI_CONNECT/DI-Connect-Metrics/MetricsMaxMetData_*.json

Uso (CLI, pasta já extraída):
    python -m app.import_vo2max "C:\\Users\\Jonas\\Downloads\\Treino"

Ou, pelo próprio painel (aba lateral), suba o .zip do export direto — ver
`import_vo2max_from_zip`, usada pelo `st.file_uploader` do dashboard. Não precisa
extrair o zip inteiro (pode ter dezenas/centenas de MB de backups/fotos): só os
MetricsMaxMetData_*.json são lidos.

Reimportar é sempre seguro: a chave (date, sport) atualiza o valor em vez de duplicar.
"""

from __future__ import annotations

import fnmatch
import json
import sys
import zipfile
from pathlib import Path
from typing import BinaryIO

from . import db

FILE_GLOB = "MetricsMaxMetData_*.json"


def _parse_maxmet_json(raw: bytes | str) -> list[dict]:
    data = json.loads(raw)
    out = []
    for rec in data:
        date = rec.get("calendarDate")
        sport = rec.get("sport")
        value = rec.get("vo2MaxValue")
        if date and sport and value:
            out.append({"date": date, "sport": sport, "value": float(value)})
    return out


def _save_records(records: dict[tuple[str, str], float]) -> int:
    db.init_db()
    with db.connect() as conn:
        for (date, sport), value in records.items():
            conn.execute(
                "INSERT INTO vo2max_history(date, sport, value, source) "
                "VALUES (?, ?, ?, 'garmin_export') "
                "ON CONFLICT(date, sport) DO UPDATE SET value=excluded.value",
                (date, sport, value),
            )
    return len(records)


def import_vo2max(export_path: str | Path) -> int:
    """Importa a partir de uma pasta já extraída do export (busca recursiva)."""
    root = Path(export_path).expanduser()
    files = sorted(root.rglob(FILE_GLOB))
    if not files:
        raise FileNotFoundError(f"Nenhum {FILE_GLOB} encontrado em {root}")

    records: dict[tuple[str, str], float] = {}
    for f in files:
        for rec in _parse_maxmet_json(f.read_text(encoding="utf-8")):
            records[(rec["date"], rec["sport"])] = rec["value"]
    return _save_records(records)


def import_vo2max_from_zip(zip_source: str | Path | BinaryIO) -> int:
    """Importa direto de um .zip (o export completo da Garmin, sem extrair)."""
    with zipfile.ZipFile(zip_source) as zf:
        names = [n for n in zf.namelist() if fnmatch.fnmatch(Path(n).name, FILE_GLOB)]
        if not names:
            raise FileNotFoundError(f"Nenhum {FILE_GLOB} encontrado dentro do zip")

        records: dict[tuple[str, str], float] = {}
        for name in names:
            for rec in _parse_maxmet_json(zf.read(name)):
                records[(rec["date"], rec["sport"])] = rec["value"]
    return _save_records(records)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "Downloads" / "Treino")
    n = import_vo2max(path)
    print(f"{n} registros de VO2max importados de {path}")

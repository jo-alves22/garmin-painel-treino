"""Painel de treino (Streamlit).

Rodar:  streamlit run app/dashboard.py     (a partir de ~/workspace/garmin)

Abas:
  • Visão Geral — como estou agora + semana atual (feito/faltando)
  • Evolução    — VO2max(proxy/VDOT), volume, 80/20, eficiência, recuperação
  • Plano       — editar o plano e ver o casamento com o executado
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import pandas as pd
import streamlit as st

# Permite rodar via `streamlit run app/dashboard.py` (importa como pacote `app`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db, import_vo2max, metrics, plan, sync  # noqa: E402
from app.garmin_client import GarminError  # noqa: E402

st.set_page_config(page_title="Painel de Treino", page_icon="🏃", layout="wide")
db.init_db()

RUN_FILTER = "('running','treadmill_running','trail_running','track_running','indoor_running')"


# --------------------------------------------------------------------------
# Carga de dados (cacheada; limpa no sync)
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_activities() -> pd.DataFrame:
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM activities ORDER BY start_local", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["km"] = df["distance_m"] / 1000.0
        df["is_run"] = df["type_key"].str.contains("running", na=False)
        df["pace_s_km"] = df["avg_speed_ms"].apply(metrics.pace_from_speed)
    return df


@st.cache_data(show_spinner=False)
def load_wellness() -> pd.DataFrame:
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM wellness ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["sleep_h"] = df["sleep_s"] / 3600.0
    return df


def current_iso_week() -> str:
    y, w, _ = dt.date.today().isocalendar()
    return f"{y}-W{w:02d}"


def best_vdot_recent(acts: pd.DataFrame, weeks: int = 6) -> float | None:
    if acts.empty or acts["vdot"].dropna().empty:
        return None
    cutoff = pd.Timestamp(dt.date.today() - dt.timedelta(weeks=weeks))
    recent = acts[(acts["date"] >= cutoff) & acts["vdot"].notna()]
    pool = recent if not recent.empty else acts[acts["vdot"].notna()]
    return float(pool["vdot"].max()) if not pool.empty else None


@st.cache_data(show_spinner=False)
def load_vo2max(sport: str = "RUNNING") -> pd.DataFrame:
    with db.connect() as conn:
        df = pd.read_sql(
            "SELECT date, value FROM vo2max_history WHERE sport=? ORDER BY date", conn, params=(sport,)
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def latest_vo2max(vo2: pd.DataFrame) -> float | None:
    return float(vo2["value"].iloc[-1]) if not vo2.empty else None


# --------------------------------------------------------------------------
# Sidebar — sync
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("🏃 Painel de Treino")
    weeks = st.slider("Janela de sincronização (semanas)", 4, 52, 16, step=4)
    if st.button("🔄 Atualizar dados (sync)", width='stretch', type="primary"):
        try:
            with st.spinner("Buscando dados da Garmin..."):
                res = sync.run_sync(weeks)
            st.cache_data.clear()
            w = res["wellness"]
            st.success(f"OK — {res['activities']} atividades · bem-estar: "
                       f"RHR {w['rhr']}, HRV {w['hrv']}, sono {w['sleep']}, peso {w['weight']}")
        except GarminError as e:
            st.error(f"Erro na API: {e}")
        except Exception as e:  # noqa: BLE001
            st.error(f"Falha no sync: {e}")

    last_sync = db.get_meta("last_sync")
    st.caption(f"Último sync: {last_sync or '— (clique em Atualizar)'}")
    st.caption(f"Janela armazenada: {db.get_meta('sync_window') or '—'}")

    st.divider()
    st.caption("📦 Importar export completo da Garmin (account.garmin.com → "
               "Conta → Exportar seus dados) para trazer o VO2max oficial do relógio — "
               "a API não expõe esse número.")
    zip_file = st.file_uploader("Export da Garmin (.zip)", type="zip", key="vo2max_zip")
    if zip_file is not None:
        try:
            with st.spinner("Lendo o zip e importando VO2max..."):
                n = import_vo2max.import_vo2max_from_zip(zip_file)
            st.cache_data.clear()
            st.success(f"OK — {n} registros de VO2max importados do export.")
        except FileNotFoundError as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"Falha ao importar o zip: {e}")

acts = load_activities()
well = load_wellness()
runs = acts[acts["is_run"]] if not acts.empty else acts

if acts.empty:
    st.info("Sem dados ainda. Clique em **🔄 Atualizar dados (sync)** na barra lateral.")
    st.stop()

tab_hoje, tab_evo, tab_plano = st.tabs(["📊 Visão Geral", "📈 Evolução", "📅 Plano"])

# ==========================================================================
# ABA 1 — VISÃO GERAL
# ==========================================================================
with tab_hoje:
    iso = current_iso_week()
    status = plan.week_status(iso)
    vdot = best_vdot_recent(runs)
    vo2_real = latest_vo2max(load_vo2max())

    st.subheader(f"Como estou — semana {iso}")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    if vo2_real:
        c1.metric("VO2max (Garmin)", f"{vo2_real:.0f}",
                  help="Valor oficial do relógio (Firstbeat), importado do export GDPR da "
                       f"Garmin — a Personal API não expõe esse número. VDOT estimado dos "
                       f"treinos: {vdot:.1f}." if vdot else "")
    else:
        c1.metric("VO2max (proxy/VDOT)", f"{vdot:.1f}" if vdot else "—",
                  help="Melhor VDOT estimado nos últimos ~6 treinos fortes. Alvo sub-20: ~54–57. "
                       "Importe o export GDPR (python -m app.import_vo2max) para ver o VO2max "
                       "oficial do relógio aqui.")
    c2.metric("Previsão 5K", metrics.time_str(metrics.race_time(vdot, 5000)) if vdot else "—",
              help="Calculada do seu VDOT (VDOT/Daniels), não do Garmin.")
    c3.metric("Previsão 10K", metrics.time_str(metrics.race_time(vdot, 10000)) if vdot else "—")
    rhr = well["resting_hr"].dropna()
    c4.metric("FC repouso", f"{int(rhr.iloc[-1])} bpm" if not rhr.empty else "—")
    hrv = well["hrv_last_night"].dropna()
    c5.metric("HRV (noite)", f"{int(hrv.iloc[-1])} ms" if not hrv.empty else "—")
    wkg = well["weight_kg"].dropna()
    c6.metric("Peso", f"{wkg.iloc[-1]:.1f} kg" if not wkg.empty else "—")

    st.divider()
    cL, cR = st.columns([1, 1])

    with cL:
        st.markdown(f"#### Semana atual · {status['done']}/{status['planned']} treinos")
        if status["planned"] == 0:
            st.caption("Nenhum treino planejado nesta semana. Vá na aba **Plano** para montar.")
        for s in status["sessions"]:
            mark = "✅" if s["done"] else "⬜"
            dia = dt.date.fromisoformat(s["date"]).strftime("%a %d/%m")
            st.write(f"{mark} **{s['kind']}** · {dia} — {s['title']}  \n<small>{s['target'] or ''}</small>",
                     unsafe_allow_html=True)

    with cR:
        wk_runs = runs[runs["iso_week"] == iso]
        km = wk_runs["km"].sum()
        st.markdown("#### Carga da semana")
        st.metric("Volume de corrida", f"{km:.1f} km")
        # 80/20 da semana
        z_easy = (wk_runs["hr_z1_s"].fillna(0) + wk_runs["hr_z2_s"].fillna(0)).sum()
        z_gray = wk_runs["hr_z3_s"].fillna(0).sum()
        z_hard = (wk_runs["hr_z4_s"].fillna(0) + wk_runs["hr_z5_s"].fillna(0)).sum()
        tot = z_easy + z_gray + z_hard
        if tot > 0:
            easy_pct = 100 * z_easy / tot
            st.metric("Tempo fácil (Z1–Z2)", f"{easy_pct:.0f}%",
                      delta=f"{easy_pct-80:+.0f} vs alvo 80%",
                      help="Regra 80/20: ~80% do tempo fácil. Z3 (cinza) = "
                           f"{100*z_gray/tot:.0f}%, forte (Z4–Z5) = {100*z_hard/tot:.0f}%.")
        else:
            st.caption("Sem dados de zona de FC nesta semana ainda.")

# ==========================================================================
# ABA 2 — EVOLUÇÃO
# ==========================================================================
with tab_evo:
    st.subheader("Evolução")

    # VO2max oficial (Garmin) vs proxy VDOT (treinos) — melhor por semana
    vo2_hist = load_vo2max()
    rv = runs[runs["vdot"].notna()]
    if not vo2_hist.empty or not rv.empty:
        st.markdown("**VO2max — oficial (Garmin) vs proxy (VDOT dos treinos)**")
        chart = pd.DataFrame(index=pd.Index([], name="date"))
        if not vo2_hist.empty:
            chart["VO2max (Garmin)"] = vo2_hist.set_index("date")["value"]
        if not rv.empty:
            vdot_wk = rv.groupby("iso_week")["vdot"].max()
            vdot_wk.index = pd.to_datetime(vdot_wk.index + "-1", format="%G-W%V-%u")
            chart["VDOT (melhor da semana)"] = vdot_wk
        st.line_chart(chart)
        caption = ("Oficial = direto do relógio (Firstbeat), importado do export GDPR. "
                    if not vo2_hist.empty else "")
        st.caption(caption + "Proxy = VDOT/Daniels a partir do melhor esforço de cada semana "
                   "(a Personal API não expõe a VO2max do Garmin em tempo real). "
                   "Tendência de subida = ganho de forma.")
    else:
        st.caption("Ainda sem VO2max (importe o export GDPR) nem corridas com esforço "
                   "suficiente para estimar VDOT.")

    c1, c2 = st.columns(2)
    with c1:
        vol_wk = runs.groupby("iso_week")["km"].sum().rename("km/semana")
        st.markdown("**Volume semanal (km)**")
        st.bar_chart(vol_wk)
    with c2:
        # 80/20 por semana (% fácil)
        g = runs.groupby("iso_week").agg(
            easy=("hr_z1_s", lambda x: x.fillna(0).sum()),
            z2=("hr_z2_s", lambda x: x.fillna(0).sum()),
            z3=("hr_z3_s", lambda x: x.fillna(0).sum()),
            z4=("hr_z4_s", lambda x: x.fillna(0).sum()),
            z5=("hr_z5_s", lambda x: x.fillna(0).sum()),
        )
        tot = g["easy"] + g["z2"] + g["z3"] + g["z4"] + g["z5"]
        easy_pct = (100 * (g["easy"] + g["z2"]) / tot).where(tot > 0).rename("% tempo fácil (Z1–Z2)")
        st.markdown("**Distribuição de intensidade — % fácil (alvo 80%)**")
        st.line_chart(easy_pct)

    c3, c4 = st.columns(2)
    with c3:
        ef = runs[runs["ef"].notna()]
        if not ef.empty:
            ef_wk = ef.groupby("iso_week")["ef"].mean().rename("Efficiency Factor")
            st.markdown("**Eficiência aeróbia (EF = m/min ÷ FC)**")
            st.line_chart(ef_wk)
            st.caption("EF subindo = mesmo pace com FC menor = motor melhorando.")
    with c4:
        if not well.empty:
            st.markdown("**Recuperação**")
            rec = well.set_index("date")[["resting_hr", "hrv_last_night", "sleep_h"]].dropna(how="all")
            st.line_chart(rec)
            st.caption("FC repouso baixa + HRV estável/alto + sono = recuperado.")

    if not well.empty and well["weight_kg"].notna().any():
        st.markdown("**Peso (kg)**")
        st.line_chart(well.set_index("date")["weight_kg"].dropna())

# ==========================================================================
# ABA 3 — PLANO
# ==========================================================================
with tab_plano:
    st.subheader("Plano de treino")
    cols = st.columns([2, 1, 1])
    week_sel = cols[0].text_input("Semana (ISO, ex.: 2026-W45)", value=current_iso_week())
    if cols[1].button("➕ Criar semana modelo", width='stretch'):
        monday = dt.date.fromisocalendar(int(week_sel[:4]), int(week_sel[-2:]), 1)
        n = plan.seed_template_week(monday)
        st.success(f"{n} sessões criadas." if n else "Semana já tinha sessões.")
        st.rerun()

    status = plan.week_status(week_sel)
    cols[2].metric("Progresso", f"{status['done']}/{status['planned']}")

    sessions = status["sessions"]
    if sessions:
        base = pd.DataFrame(sessions)[["id", "date", "kind", "title", "target", "target_km", "done", "matched_id"]]
        edited = st.data_editor(
            base, num_rows="dynamic", width='stretch', hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("id", disabled=True),
                "date": st.column_config.TextColumn("Data (YYYY-MM-DD)"),
                "kind": st.column_config.SelectboxColumn("Tipo", options=plan.KINDS),
                "title": "Título",
                "target": "Alvo",
                "target_km": st.column_config.NumberColumn("km alvo"),
                "done": st.column_config.CheckboxColumn("Feito"),
                "matched_id": st.column_config.NumberColumn("Atividade casada", disabled=True),
            },
            key=f"editor_{week_sel}",
        )
        if st.button("💾 Salvar alterações", type="primary"):
            orig_ids = {int(r["id"]) for r in sessions}
            seen = set()
            for _, row in edited.iterrows():
                rid = row.get("id")
                if pd.isna(rid):  # linha nova
                    if row.get("date") and row.get("kind") and row.get("title"):
                        plan.add_session(str(row["date"]), str(row["kind"]), str(row["title"]),
                                         str(row.get("target") or ""),
                                         float(row["target_km"]) if pd.notna(row.get("target_km")) else None)
                else:
                    rid = int(rid)
                    seen.add(rid)
                    plan.update_session(rid, date=str(row["date"]), kind=str(row["kind"]),
                                        title=str(row["title"]), target=str(row.get("target") or ""),
                                        target_km=float(row["target_km"]) if pd.notna(row.get("target_km")) else None,
                                        done=int(bool(row["done"])))
            for rid in orig_ids - seen:  # linhas removidas
                plan.delete_session(rid)
            st.success("Plano salvo.")
            st.rerun()
    else:
        st.caption("Semana vazia. Use **Criar semana modelo** ou adicione sessões abaixo.")

    with st.expander("➕ Adicionar sessão avulsa"):
        with st.form("add_sess", clear_on_submit=True):
            a, b, c = st.columns(3)
            d_in = a.date_input("Data", value=dt.date.today())
            k_in = b.selectbox("Tipo", plan.KINDS)
            t_in = c.text_input("Título")
            tgt = st.text_input("Alvo (pace/distância/séries)")
            km_in = st.number_input("km alvo (opcional)", min_value=0.0, value=0.0, step=1.0)
            if st.form_submit_button("Adicionar") and t_in:
                plan.add_session(d_in.isoformat(), k_in, t_in, tgt, km_in or None)
                st.success("Sessão adicionada.")
                st.rerun()

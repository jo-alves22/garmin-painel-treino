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

import altair as alt
import pandas as pd
import streamlit as st

# Permite rodar via `streamlit run app/dashboard.py` (importa como pacote `app`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db, goals, import_plan, import_vo2max, metrics, plan, plan_generator, sync  # noqa: E402
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


def week_monday(iso_week) -> pd.Series:
    """Converte 'YYYY-Www' (segunda-feira ISO) em data real — p/ eixo dos gráficos
    em vez do código de semana, que ninguém lê de cabeça."""
    return pd.to_datetime(pd.Series(iso_week).astype(str) + "-1", format="%G-W%V-%u")


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

    last_sync_raw = db.get_meta("last_sync")
    sync_str = dt.datetime.fromisoformat(last_sync_raw).strftime("%d/%m/%Y %H:%M") if last_sync_raw else None
    st.subheader(f"Como estou — dados de {sync_str}" if sync_str else "Como estou — ainda sem sync")
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
        frames = []
        if not vo2_hist.empty:
            frames.append(vo2_hist.rename(columns={"value": "valor"}).assign(série="VO2max (Garmin)"))
        if not rv.empty:
            vdot_wk = rv.groupby("iso_week")["vdot"].max().reset_index()
            vdot_wk["date"] = week_monday(vdot_wk["iso_week"])
            frames.append(vdot_wk.rename(columns={"vdot": "valor"})[["date", "valor"]]
                          .assign(série="VDOT (melhor da semana)"))
        long_df = pd.concat(frames, ignore_index=True)
        vo2_chart = alt.Chart(long_df).mark_line().encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%b/%Y", labelAngle=-45)),
            y=alt.Y("valor:Q", title=None),
            color=alt.Color("série:N", title=None),
            tooltip=[alt.Tooltip("date:T", title="Data", format="%d/%m/%Y"),
                     alt.Tooltip("valor:Q", title="Valor"), "série:N"],
        )
        st.altair_chart(vo2_chart, width='stretch')
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
        vol_wk = runs.groupby("iso_week")["km"].sum().reset_index(name="km")
        vol_wk["semana"] = week_monday(vol_wk["iso_week"]).dt.strftime("%d/%m/%Y")
        st.markdown("**Volume semanal (km)**")
        vol_chart = alt.Chart(vol_wk).mark_bar().encode(
            x=alt.X("semana:N", sort=vol_wk["semana"].tolist(), title=None,
                     axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("km:Q", title="km/semana"),
            tooltip=["semana", alt.Tooltip("km:Q", format=".1f")],
        )
        st.altair_chart(vol_chart, width='stretch')
    with c2:
        # 80/20 por semana (% fácil)
        g = runs.groupby("iso_week").agg(
            easy=("hr_z1_s", lambda x: x.fillna(0).sum()),
            z2=("hr_z2_s", lambda x: x.fillna(0).sum()),
            z3=("hr_z3_s", lambda x: x.fillna(0).sum()),
            z4=("hr_z4_s", lambda x: x.fillna(0).sum()),
            z5=("hr_z5_s", lambda x: x.fillna(0).sum()),
        ).reset_index()
        tot = g["easy"] + g["z2"] + g["z3"] + g["z4"] + g["z5"]
        g["easy_pct"] = (100 * (g["easy"] + g["z2"]) / tot).where(tot > 0)
        g["semana"] = week_monday(g["iso_week"]).dt.strftime("%d/%m/%Y")
        st.markdown("**Distribuição de intensidade — % fácil (alvo 80%)**")
        intens_chart = alt.Chart(g.dropna(subset=["easy_pct"])).mark_line(point=True).encode(
            x=alt.X("semana:N", sort=g["semana"].tolist(), title=None,
                     axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("easy_pct:Q", title="% fácil"),
            tooltip=["semana", alt.Tooltip("easy_pct:Q", title="% fácil", format=".0f")],
        )
        st.altair_chart(intens_chart, width='stretch')

    c3, c4 = st.columns(2)
    with c3:
        ef = runs[runs["ef"].notna()]
        if not ef.empty:
            ef_wk = ef.groupby("iso_week")["ef"].mean().reset_index(name="ef")
            ef_wk["semana"] = week_monday(ef_wk["iso_week"]).dt.strftime("%d/%m/%Y")
            st.markdown("**Eficiência aeróbia (EF = m/min ÷ FC)**")
            ef_chart = alt.Chart(ef_wk).mark_line(point=True).encode(
                x=alt.X("semana:N", sort=ef_wk["semana"].tolist(), title=None,
                         axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("ef:Q", title="EF"),
                tooltip=["semana", alt.Tooltip("ef:Q", title="EF", format=".3f")],
            )
            st.altair_chart(ef_chart, width='stretch')
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

    with st.expander("🎯 Objetivo e disponibilidade"):
        goal = goals.get_goal()
        avail = goals.get_availability()

        gc1, gc2, gc3 = st.columns(3)
        dist = gc1.number_input("Distância alvo (km)", min_value=0.1, value=goal["distance_km"], step=0.5)
        tmin = gc2.number_input("Tempo alvo — min", min_value=0, value=goal["time_s"] // 60, step=1)
        tsec = gc3.number_input("Tempo alvo — seg", min_value=0, max_value=59, value=goal["time_s"] % 60, step=1)

        has_date = st.checkbox("Tenho uma data de prova definida", value=bool(goal["date"]))
        race_date = None
        if has_date:
            race_date = st.date_input(
                "Data da prova",
                value=dt.date.fromisoformat(goal["date"]) if goal["date"] else dt.date.today(),
                format="DD/MM/YYYY",
            )

        st.caption("Dias disponíveis pra treinar")
        dias_sel = st.multiselect(
            "Dias disponíveis pra treinar", options=list(range(7)), default=avail,
            format_func=lambda d: goals.WEEKDAY_NAMES[d], label_visibility="collapsed",
        )

        if st.button("💾 Salvar objetivo"):
            goals.set_goal(dist, int(tmin) * 60 + int(tsec), race_date.isoformat() if race_date else None)
            goals.set_availability(dias_sel)
            st.success("Objetivo e disponibilidade salvos.")

    with st.expander("📥 Importar planilha de plano"):
        st.caption("Aceita planilha no formato das suas planilhas de plano (aba "
                   "'📆 Calendário', linhas 'DD/MM | Dia | sessão'). "
                   "⚠️ **Substitui TODAS as sessões do plano atual** — as geradas pelo "
                   "motor de regras ou criadas à mão também.")
        ic1, ic2 = st.columns([1, 2])
        plan_year = ic1.number_input("Ano da planilha", min_value=2020, max_value=2100,
                                      value=dt.date.today().year, step=1)
        confirm_replace = ic2.checkbox("Confirmo que quero substituir o plano atual pela planilha")
        plan_file = st.file_uploader("Planilha do plano (.xlsx)", type="xlsx", key="plan_xlsx",
                                      disabled=not confirm_replace)
        if plan_file is not None and confirm_replace:
            try:
                n = import_plan.import_plan(plan_file, int(plan_year))
                st.cache_data.clear()
                st.success(f"{n} sessões importadas da planilha.")
                st.rerun()
            except KeyError:
                st.error(f"Não encontrei a aba '{import_plan.CAL_SHEET}' nessa planilha.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Falha ao importar a planilha: {e}")

    st.markdown("#### 🔭 Próximos treinos")
    horizon = st.slider("Ver quantas semanas à frente", 1, 8, 4, key="plano_horizon")
    future_sessions = plan.upcoming(days=horizon * 7)
    if future_sessions:
        df_up = pd.DataFrame(future_sessions)
        df_up["Dia"] = pd.to_datetime(df_up["date"]).dt.strftime("%a %d/%m")
        df_up = df_up.rename(columns={"kind": "Tipo", "title": "Título",
                                       "target": "Alvo", "target_km": "km alvo"})
        st.dataframe(df_up[["Dia", "Tipo", "Título", "Alvo", "km alvo"]],
                     width='stretch', hide_index=True)
    else:
        st.caption(f"Nada programado (e não feito) nas próximas {horizon} semana(s) — "
                   "monte as semanas no editor abaixo.")

    st.divider()
    st.markdown("#### ✏️ Editar semana")

    if "plano_dia_ref" not in st.session_state:
        st.session_state.plano_dia_ref = dt.date.today()

    nav = st.columns([1, 1, 2, 1, 1])
    # Os botões que alteram plano_dia_ref precisam ser instanciados (e resolvidos)
    # ANTES do date_input com essa mesma key — o Streamlit não deixa mexer no
    # session_state de um widget já criado na mesma execução do script.
    prev_clicked = nav[0].button("◀ Anterior", width='stretch')
    hoje_clicked = nav[1].button("Hoje", width='stretch')
    next_clicked = nav[3].button("Próxima ▶", width='stretch')
    if prev_clicked:
        st.session_state.plano_dia_ref -= dt.timedelta(weeks=1)
    if hoje_clicked:
        st.session_state.plano_dia_ref = dt.date.today()
    if next_clicked:
        st.session_state.plano_dia_ref += dt.timedelta(weeks=1)
    dia_ref = nav[2].date_input("Um dia da semana", key="plano_dia_ref", format="DD/MM/YYYY")
    if nav[4].button("➕ Semana modelo", width='stretch'):
        n = plan.seed_template_week(plan.monday_of(dia_ref))
        st.success(f"{n} sessões criadas." if n else "Semana já tinha sessões.")
        st.rerun()

    monday = plan.monday_of(dia_ref)
    sunday = monday + dt.timedelta(days=6)
    week_sel = plan.iso_week_of(dia_ref.isoformat())
    st.caption(f"Semana de {monday.strftime('%d/%m/%Y')} a {sunday.strftime('%d/%m/%Y')}")

    status = plan.week_status(week_sel)

    gen_disabled = status["planned"] > 0
    gen_help = ("Usa VDOT/VO2max, objetivo, dias disponíveis e recuperação recente pra montar "
                "a semana (regras de Daniels/80-20, sem IA). Só em semana vazia — apague as "
                "sessões antes de gerar de novo." if gen_disabled else None)
    if st.button("🧠 Gerar com motor de regras", width='stretch', disabled=gen_disabled, help=gen_help):
        try:
            sessions = plan_generator.generate_week_plan(monday)
            n = plan_generator.apply_week_plan(monday, sessions)
            st.success(f"{n} sessões geradas a partir do seu histórico e objetivo.")
            st.rerun()
        except plan_generator.PlanGeneratorError as e:
            st.error(str(e))

    st.metric("Progresso", f"{status['done']}/{status['planned']}")

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

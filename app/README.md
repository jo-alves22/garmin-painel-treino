# 🏃 Painel de Treino (Garmin Personal API)

Painel local que mostra, num lugar só: **como estou**, **evolução** (VO2max/proxy, volume,
80/20, eficiência, recuperação) e **o plano da semana** (o que já fiz e o que falta).

Os dados vêm da **Garmin Personal API** (a mesma `api_Key`, em `.env`, validada pelo
`verify_garmin_api.sh`). A previsão de prova **não existe** na Personal API, então é
**estimada** aqui a partir dos seus treinos (VDOT/Daniels); o VO2max oficial do relógio vem do
export GDPR da Garmin (ver seção **VO2max oficial** abaixo).

## Como rodar

```bash
cd ~/workspace/garmin
cp .env.example .env     # preencha GARMIN_API_KEY com a sua api_Key
./start.sh               # Linux/macOS — abre em http://localhost:8501
./start.ps1               # Windows (PowerShell)
```

Na barra lateral, clique em **🔄 Atualizar dados (sync)** para puxar da Garmin. Dá para
sincronizar sem a interface:

```bash
python3 -m app.sync 16   # últimas 16 semanas
```

## Abas

- **📊 Visão Geral** — cards (VO2max — oficial do Garmin se já importado, senão proxy VDOT —,
  previsão 5K/10K, FC repouso, HRV, peso) + a semana atual com treinos feitos/faltando + volume
  e 80/20 da semana.
- **📈 Evolução** — VO2max oficial × VDOT (melhor por semana), volume semanal, % tempo fácil
  (alvo 80%), Efficiency Factor, e recuperação (FC repouso / HRV / sono / peso).
- **📅 Plano** — monte e edite o plano dentro do app. "Criar semana modelo" gera um esqueleto
  (1 qualidade + 1 longo + força + easy) editável. O app **casa** cada sessão planejada com a
  corrida/força executada na mesma semana e marca ✅.

## Como as métricas são calculadas

- **VO2max (proxy)** = melhor **VDOT** dos ~6 treinos fortes recentes (fórmula de Daniels a
  partir de distância+tempo). A mediana é baixa de propósito: easy runs rendem VDOT baixo; o que
  importa é o **teto** (melhor esforço), por isso usamos o máximo.
- **Previsão 5K/10K** = inverte o VDOT para o tempo daquela distância (independe do Garmin).
- **80/20** = tempo em Z1–Z2 (fácil) vs Z3 (cinza) vs Z4–Z5 (forte), a partir de
  `hrTimeInZone_*` de cada corrida.
- **Efficiency Factor (EF)** = velocidade (m/min) ÷ FC média. Subindo = motor aeróbio melhorando.

## VO2max oficial (import do export Garmin)

A Personal API não expõe o `vo2MaxValue` do relógio (só rótulos como "IMPROVING_VO2_MAX_15").
O número oficial (Firstbeat) só existe no export completo da conta
(account.garmin.com → **Conta → Exportar seus dados**). Para trazer esse número ao painel:

- **Pelo app**: barra lateral → solte o `.zip` do export em **"Export da Garmin (.zip)"**. Lê só
  os `MetricsMaxMetData_*.json` de dentro do zip, sem extrair o resto (backups, fotos, FITs).
- **Por CLI** (pasta já extraída): `python -m app.import_vo2max ~/Downloads/Treino`

Reimportar é seguro (atualiza em vez de duplicar). Como o export é um processo manual do Garmin
(não é instantâneo), repita sempre que quiser atualizar esse número.

## Sync automático (opcional)

Para manter sempre fresco sem abrir o painel, agende no cron (ex.: a cada 3h):

```cron
0 */3 * * * cd ~/workspace/garmin && /usr/bin/env python3 -m app.sync 8 >> ~/workspace/garmin/sync.log 2>&1
```

## Arquivos

```
app/
  garmin_client.py   api_Key (.env) -> token (JWT) + GETs à Personal API
  db.py              esquema SQLite (atividades, bem-estar, plano, vo2max_history)
  sync.py            API -> SQLite (janelas fatiadas p/ limites de 31 dias)
  metrics.py         VDOT, previsão de prova, EF, 80/20
  import_vo2max.py   VO2max oficial a partir do export GDPR (pasta ou .zip)
  plan.py            CRUD do plano + casamento planejado×executado
  dashboard.py       interface Streamlit (3 abas)
.env                 GARMIN_API_KEY (copie de .env.example — nunca versionar)
garmin_painel.db     banco local (gerado no 1º sync) — dados pessoais, não compartilhar
```

> ⚠️ `garmin_painel.db`, `.env` e `~/.config/garmin-painel/bearer.json` contêm dados
> pessoais/credenciais. Não versione nem compartilhe (já estão no `.gitignore`).

#!/usr/bin/env bash
#
# verify_garmin_api.sh — Verifica a Garmin Personal API ponta a ponta.
#
# 1. Lê GARMIN_API_KEY de .env
# 2. Troca a key por um token (JWT) no personalapitoken-service
# 3. Chama todos os ~24 endpoints (GET) e imprime um relatório de status
# 4. Salva as respostas de exemplo em ./responses/
#
# Dependências: curl, jq, python3 (todos já presentes no ambiente).
#
# ATENÇÃO: as respostas em ./responses/ contêm dados pessoais de saúde.
# Não commite nem compartilhe esse diretório nem o .env.

set -u

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
BASE_URL="https://personal.connectapi.garmin.com"
TOKEN_PATH="/personalapitoken-service/personal-api/personal-api-key/token"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
RESP_DIR="${SCRIPT_DIR}/responses"
mkdir -p "${RESP_DIR}"

# Datas (hoje = 2026-11-05). Dia anterior tende a ter dados completos.
TODAY="$(date +%F)"
DATE="$(date -d 'yesterday' +%F)"
START="$(date -d '7 days ago' +%F)"
END="${TODAY}"

# Cores (desativadas se não for TTY)
if [ -t 1 ]; then
  G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[1m'; C=$'\e[36m'; N=$'\e[0m'
else
  G=''; R=''; Y=''; B=''; C=''; N=''
fi

# Contadores
OK=0; EMPTY=0; FAIL=0

# --------------------------------------------------------------------------
# Pré-checagens
# --------------------------------------------------------------------------
for bin in curl jq python3; do
  command -v "$bin" >/dev/null 2>&1 || { echo "${R}Falta a dependência: $bin${N}"; exit 1; }
done
[ -f "${ENV_FILE}" ] || { echo "${R}.env não encontrado em ${ENV_FILE} (copie .env.example)${N}"; exit 1; }

API_KEY="$(grep -m1 '^GARMIN_API_KEY=' "${ENV_FILE}" | cut -d'=' -f2- | tr -d '"'"'"'\r')"
[ -n "${API_KEY}" ] || { echo "${R}GARMIN_API_KEY ausente/vazia em ${ENV_FILE}${N}"; exit 1; }

echo "${B}== Garmin Personal API — verificação ==${N}"
echo "Base URL : ${BASE_URL}"
echo "Datas    : única=${DATE}  intervalo=${START}..${END}  (hoje=${TODAY})"
echo

# --------------------------------------------------------------------------
# 1) Obter token
# --------------------------------------------------------------------------
echo "${B}[1] Obtendo token (POST ${TOKEN_PATH})...${N}"
TOKEN_BODY="$(curl -s -S -w $'\n%{http_code}' -X POST "${BASE_URL}${TOKEN_PATH}" \
  -H "Authorization: ApiKey ${API_KEY}" -H "Accept: */*")"
TOKEN_CODE="$(printf '%s' "${TOKEN_BODY}" | tail -n1)"
JWT="$(printf '%s' "${TOKEN_BODY}" | sed '$d' | tr -d '[:space:]')"

if [ "${TOKEN_CODE}" != "200" ] || [ -z "${JWT}" ]; then
  echo "${R}✗ Falha ao obter token (HTTP ${TOKEN_CODE}).${N}"
  printf '%s\n' "${TOKEN_BODY}" | sed '$d'
  exit 1
fi

# Validade (claim exp do JWT) sem verificar assinatura
EXP_HUMAN="$(python3 - "$JWT" <<'PY'
import sys, json, base64, datetime
tok = sys.argv[1]
try:
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload))
    exp = data.get("exp")
    if exp:
        dt = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc).astimezone()
        print(dt.strftime("%Y-%m-%d %H:%M:%S %Z"))
    else:
        print("(sem claim exp)")
except Exception as e:
    print(f"(não foi possível decodificar: {e})")
PY
)"
echo "${G}✓ Token obtido${N} — válido até ${C}${EXP_HUMAN}${N}  (JWT: ${#JWT} chars)"
echo

# --------------------------------------------------------------------------
# Helper: check "<nome>" "<path>" [accept] [out-ext]
# --------------------------------------------------------------------------
check() {
  local name="$1" path="$2" accept="${3:-application/json}" ext="${4:-json}"
  local out="${RESP_DIR}/${name}.${ext}"
  local code
  code="$(curl -s -S -o "${out}" -w '%{http_code}' \
    -H "Authorization: Bearer ${JWT}" -H "Accept: ${accept}" \
    "${BASE_URL}${path}")"

  local size; size="$(wc -c < "${out}" | tr -d ' ')"
  local label snippet trimmed
  # Corpo "vazio" = sem conteúdo útil (vazio, null, [], {}).
  # Para downloads binários (zip) basta olhar o tamanho — não lemos o conteúdo.
  if [ "${ext}" = "zip" ]; then
    [ "${size}" -gt 0 ] && trimmed="zip" || trimmed=""
  else
    trimmed="$(tr -d '[:space:]' < "${out}")"
  fi

  if [ "${code}" -ge 200 ] && [ "${code}" -lt 300 ]; then
    if [ -z "${trimmed}" ] || [ "${trimmed}" = "null" ] || [ "${trimmed}" = "[]" ] || [ "${trimmed}" = "{}" ]; then
      label="${Y}OK (vazio)${N}"; EMPTY=$((EMPTY+1))
    else
      label="${G}OK${N}"; OK=$((OK+1))
    fi
  else
    label="${R}ERRO${N}"; FAIL=$((FAIL+1))
  fi

  if [ "${ext}" = "zip" ]; then
    snippet="$(file -b "${out}" 2>/dev/null || echo binário)"
  else
    snippet="$(jq -c 'if type=="array" then {_array_len: length, _first: .[0]} else . end' "${out}" 2>/dev/null | head -c 120)"
    [ -z "${snippet}" ] && snippet="$(head -c 120 "${out}" | tr '\n' ' ')"
  fi

  printf '  %-28s HTTP %s  %-12b %6s B  %s\n' "${name}" "${code}" "${label}" "${size}" "${snippet}"
}

# --------------------------------------------------------------------------
# 2) Atividades (primeiro, para capturar um activityId real)
# --------------------------------------------------------------------------
echo "${B}[2] Atividades${N}"
check "activities" "/activitylist-service/activities/search/activities?start=0&limit=5"

ACT_ID="$(jq -r 'if type=="array" then .[0].activityId else (.activityList[0].activityId // empty) end' \
  "${RESP_DIR}/activities.json" 2>/dev/null)"

if [ -n "${ACT_ID}" ] && [ "${ACT_ID}" != "null" ]; then
  echo "  ${C}→ usando activityId=${ACT_ID} para os endpoints encadeados${N}"
  check "activity-detail"   "/activity-service/activity/${ACT_ID}"
  check "activity-workouts" "/activity-service/activity/${ACT_ID}/workouts"
  check "activity-download" "/download-service/files/activity/${ACT_ID}" \
        "application/zip, application/x-zip-compressed, application/octet-stream" "zip"
else
  echo "  ${Y}(nenhuma atividade encontrada — pulando activity-detail / workouts / download)${N}"
fi
echo

# --------------------------------------------------------------------------
# 3) Endpoints sem parâmetro
# --------------------------------------------------------------------------
echo "${B}[3] Perfil / configurações${N}"
check "profile-settings"  "/userprofile-service/userprofile/settings"
check "heart-rate-zones"  "/biometric-service/heartRateZones/v2"
echo

# --------------------------------------------------------------------------
# 4) Endpoints de data única (DATE)
# --------------------------------------------------------------------------
echo "${B}[4] Métricas de data única (${DATE})${N}"
check "sleep"                   "/sleep-service/sleep/dailySleep?date=${DATE}"
check "heart-rate"              "/wellness-service/wellness/dailyHeartRate?date=${DATE}"
check "hrv"                     "/hrv-service/hrv/${DATE}"
check "spo2"                    "/wellness-service/wellness/daily/spo2acclimation/${DATE}"
check "fitness-age"            "/fitnessage-service/fitnessage/v2/${DATE}"
check "food-logs"              "/nutrition-service/food/logs/${DATE}"
check "menstrual-summary"      "/periodichealth-service/menstrualcycle/summary/${DATE}"
check "menstrual-day-view"     "/periodichealth-service/menstrualcycle/dayview/${DATE}?todayDate=${TODAY}"
echo

# --------------------------------------------------------------------------
# 5) Endpoints de intervalo (START..END)
# --------------------------------------------------------------------------
echo "${B}[5] Métricas por intervalo (${START}..${END})${N}"
check "daily-summaries"        "/usersummary-service/v2/dailysummaries?startDate=${START}&endDate=${END}"
check "stress"                 "/usersummary-service/stats/stress/daily/${START}/${END}"
check "steps"                  "/usersummary-service/stats/steps/daily/${START}/${END}"
check "hydration"              "/usersummary-service/stats/hydration/daily/${START}/${END}"
check "sleep-summaries"        "/sleep-service/sleep/dailySleepSummariesByDate?startDate=${START}&endDate=${END}"
check "body-battery"           "/wellness-service/wellness/bodyBattery/reports/daily?startDate=${START}&endDate=${END}"
check "hrv-summaries"          "/hrv-service/hrv/daily/${START}/${END}"
check "weight-measurements"    "/weight-service/weight/range/${START}/${END}?includeAll=true"
check "effective-weight-goals" "/goal-service/goal/user/effective/weightgoal/${START}/${END}"
check "wellness-activity-sum"  "/wellnessactivity-service/activity/summary/range/${START}/${END}"
echo

# --------------------------------------------------------------------------
# Resumo
# --------------------------------------------------------------------------
echo "${B}== Resumo ==${N}"
echo "  ${G}OK com dados : ${OK}${N}"
echo "  ${Y}OK sem dados : ${EMPTY}${N}"
echo "  ${R}Com erro     : ${FAIL}${N}"
echo
echo "Respostas salvas em: ${RESP_DIR}"
echo "${Y}Lembrete: ./responses/ e .env contêm dados pessoais/credenciais — não compartilhe.${N}"

# Exit != 0 só se houve erro de verdade (ignora 'sem dados')
[ "${FAIL}" -eq 0 ]

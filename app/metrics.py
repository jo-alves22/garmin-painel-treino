"""Métricas derivadas dos treinos (a Personal API não expõe VO2max/previsões).

Núcleo:
  - vdot_from_effort: estimativa de VDOT (≈ VO2max) a partir de um esforço (dist, tempo)
    pela fórmula de Jack Daniels/Gilbert. O melhor VDOT de uma janela recente serve de
    proxy da VO2max atual; a tendência mostra evolução.
  - race_time: previsão de tempo para uma distância dado um VDOT (inverte a fórmula).
  - efficiency_factor: (m/min)/FC — sobe quando o motor aeróbio melhora ("mesmo pace, FC menor").
  - intensity_split: distribuição 80/20 a partir do tempo em zonas de FC.
"""

from __future__ import annotations

import math

# Só corridas de rua/esteira entram no cálculo de VDOT.
RUN_TYPES = {"running", "treadmill_running", "trail_running", "track_running", "indoor_running"}


def _pct_max(t_min: float) -> float:
    """Fração da VO2max sustentável por t_min minutos (Daniels)."""
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def _vo2_cost(v_m_per_min: float) -> float:
    """Custo de O2 (ml/kg/min) para correr a v metros/min (Daniels)."""
    return -4.60 + 0.182258 * v_m_per_min + 0.000104 * v_m_per_min**2


def vdot_from_effort(distance_m: float, duration_s: float) -> float | None:
    """VDOT estimado de um esforço. None se os dados não fizerem sentido."""
    if not distance_m or not duration_s or distance_m < 1000 or duration_s < 120:
        return None  # ignora tiros curtos/aquecimentos: ruído no VDOT
    t_min = duration_s / 60.0
    v = distance_m / t_min
    vdot = _vo2_cost(v) / _pct_max(t_min)
    if not (20 < vdot < 85):  # fora da faixa plausível = descarta
        return None
    return round(vdot, 1)


def race_time(vdot: float, distance_m: float) -> float:
    """Tempo previsto (segundos) para `distance_m` dado um VDOT. Busca binária no tempo."""
    lo, hi = 60.0, 6 * 3600.0
    for _ in range(60):
        mid = (lo + hi) / 2
        t_min = mid / 60.0
        v = distance_m / t_min
        vdot_mid = _vo2_cost(v) / _pct_max(t_min)
        if vdot_mid > vdot:   # está correndo rápido demais p/ esse VDOT -> dar mais tempo
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _velocity_for_vo2(vo2_cost_target: float) -> float:
    """Inverte _vo2_cost: velocidade (m/min) que custa `vo2_cost_target` ml/kg/min."""
    a, b, c = 0.000104, 0.182258, -(4.60 + vo2_cost_target)
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def _velocity_for_duration(vdot: float, t_min: float) -> float:
    """Velocidade (m/min) sustentável por t_min minutos, dado o VDOT."""
    return _velocity_for_vo2(vdot * _pct_max(t_min))


def training_paces(vdot: float) -> dict[str, float]:
    """Paces de treino (s/km) de Daniels a partir do VDOT atual:
      - easy: ~70% do VDOT (fora da curva de prova — é esforço deliberadamente
        submáximo, não "melhor esforço possível" por tempo t)
      - marathon: pace de prova de maratona (42195 m), via race_time
      - threshold: esforço sustentável por ~60 min ("confortavelmente difícil")
      - interval: esforço sustentável por ~5 min (~vVO2max)
      - repetition: esforço sustentável por ~2 min (bem mais rápido, série curta)
    """
    marathon_s = race_time(vdot, 42195.0)
    return {
        "easy": 60000.0 / _velocity_for_vo2(0.70 * vdot),
        "marathon": marathon_s / 42.195,
        "threshold": 60000.0 / _velocity_for_duration(vdot, 60),
        "interval": 60000.0 / _velocity_for_duration(vdot, 5),
        "repetition": 60000.0 / _velocity_for_duration(vdot, 2),
    }


def efficiency_factor(distance_m: float, duration_s: float, avg_hr: float | None) -> float | None:
    """EF = velocidade (m/min) / FC média. Maior = mais eficiente aerobicamente."""
    if not avg_hr or not distance_m or not duration_s:
        return None
    return round((distance_m / (duration_s / 60.0)) / avg_hr, 3)


def intensity_split(z1: float, z2: float, z3: float, z4: float, z5: float) -> dict:
    """Distribui o tempo em zonas em easy / cinza (Z3) / forte. Base da regra 80/20."""
    z = [x or 0.0 for x in (z1, z2, z3, z4, z5)]
    total = sum(z)
    if total <= 0:
        return {"easy_s": 0, "gray_s": 0, "hard_s": 0, "easy_pct": None, "hard_pct": None}
    easy, gray, hard = z[0] + z[1], z[2], z[3] + z[4]
    return {
        "easy_s": easy,
        "gray_s": gray,
        "hard_s": hard,
        "easy_pct": round(100 * easy / total, 1),
        "hard_pct": round(100 * hard / total, 1),
    }


def pace_str(seconds_per_km: float | None) -> str:
    """Formata pace em 'm:ss/km'."""
    if not seconds_per_km or seconds_per_km <= 0:
        return "—"
    m, s = divmod(int(round(seconds_per_km)), 60)
    return f"{m}:{s:02d}/km"


def time_str(seconds: float | None) -> str:
    """Formata duração em 'h:mm:ss' ou 'm:ss'."""
    if not seconds or seconds <= 0:
        return "—"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pace_from_speed(avg_speed_ms: float | None) -> float | None:
    """Converte velocidade (m/s) em pace (s/km)."""
    if not avg_speed_ms or avg_speed_ms <= 0:
        return None
    return 1000.0 / avg_speed_ms

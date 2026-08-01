"""
Scoring determinístico para research-pack.

Fórmula (0-100):
    score = trend_component * 0.3
          + volume_component * 0.2
          + ad_longevity_component * 0.3
          + saturacao_invertida * 0.2

Cada componente é normalizado para [0, 100].

Labels:
    AVANÇAR   >= 70
    OBSERVAR   40-69
    DESCARTAR  < 40

Normalização:

  trend_component:
    slope_pct é mapeado: -100..0..100 → 0..50..100
    slope_pct = 0    → 50 (neutro)
    slope_pct = +100 → 100 (crescimento forte)
    slope_pct = -100 → 0   (declínio forte)
    Fórmula: min(100, max(0, 50 + slope_pct * 0.5))
    Se não há dados de trend → 0

  volume_component:
    Volume mensal é mapeado logaritmicamente para [0, 100].
    volume = 0       → 0
    volume = 1000    → 50
    volume = 100000  → 100
    Fórmula: min(100, max(0, log10(max(volume, 1)) * 20))
    Se não há dados de volume → 0

  ad_longevity_component:
    Média de dias ativos dos top 3 ads, mapeado para [0, 100].
    avg_days = 0   → 0
    avg_days = 30  → 50
    avg_days = 180 → 100
    Fórmula: min(100, max(0, avg_days / 1.8))
    Se não há ads → 0

  saturacao_invertida:
    baixa  (< 10 ads)  → 100
    média  (10-50)     → 50
    alta   (> 50)      → 0
    Se não há dados de ads → 0
"""

import math
from typing import Any


def _trend_component(trends_data: dict[str, Any]) -> float:
    """Normaliza o slope de tendência para 0-100."""
    if "error" in trends_data or not trends_data:
        return 0.0
    slope = trends_data.get("slope_pct", 0.0)
    # slope_pct ∈ [-100, 100+]  →  [0, 100]
    # Centro em 0 = 50, cada 1% de slope = 0.5 pontos
    score = 50.0 + slope * 0.5
    return min(100.0, max(0.0, score))


def _volume_component(csv_data: dict[str, Any]) -> float:
    """Normaliza volume mensal para 0-100 (escala log)."""
    if "error" in csv_data or not csv_data:
        return 0.0
    volume = csv_data.get("volume")
    if volume is None or volume <= 0:
        return 0.0
    # log10(1) = 0, log10(1000) ≈ 3, log10(100000) = 5
    # * 20 → 0..60..100
    score = math.log10(max(float(volume), 1.0)) * 20.0
    return min(100.0, max(0.0, score))


def _ad_longevity_component(ads_data: dict[str, Any]) -> float:
    """Normaliza longevidade média dos top ads para 0-100."""
    if "error" in ads_data or not ads_data:
        return 0.0
    top_ads = ads_data.get("top_ads", [])
    if not top_ads:
        return 0.0
    avg_days = sum(a.get("days_active", 0) for a in top_ads) / len(top_ads)
    # 0 → 0, 30 → 50, 180 → 100+
    score = avg_days / 1.8
    return min(100.0, max(0.0, score))


def _saturacao_component(ads_data: dict[str, Any]) -> float:
    """Mapeia nível de saturação para score invertido."""
    if "error" in ads_data or not ads_data:
        return 0.0
    sat = ads_data.get("saturation", "baixa")
    if sat == "baixa":
        return 100.0
    elif sat == "média":
        return 50.0
    else:  # alta
        return 0.0


def compute(trends_data: dict[str, Any], csv_data: dict[str, Any], ads_data: dict[str, Any]) -> dict[str, Any]:
    """
    Calcula o score determinístico a partir dos dados dos 3 adapters.

    Args:
        trends_data: dict devolvido por adapters/trends.py
        csv_data: dict devolvido por adapters/keyword_csv.py
        ads_data: dict devolvido por adapters/apify_ads.py

    Returns:
        {
            "score": int (0-100),
            "label": "AVANÇAR" | "OBSERVAR" | "DESCARTAR",
            "breakdown": {
                "trend": float (peso 0.3),
                "volume": float (peso 0.2),
                "ad_longevity": float (peso 0.3),
                "saturacao_invertida": float (peso 0.2),
            }
        }
    """
    t = _trend_component(trends_data)
    v = _volume_component(csv_data)
    a = _ad_longevity_component(ads_data)
    s = _saturacao_component(ads_data)

    score = round(t * 0.3 + v * 0.2 + a * 0.3 + s * 0.2)

    if score >= 70:
        label = "AVANÇAR"
    elif score >= 40:
        label = "OBSERVAR"
    else:
        label = "DESCARTAR"

    return {
        "score": score,
        "label": label,
        "breakdown": {
            "trend": round(t, 1),
            "volume": round(v, 1),
            "ad_longevity": round(a, 1),
            "saturacao_invertida": round(s, 1),
        },
    }

"""Estimación del TEV (Término de Energía Variable) 3.0TD."""
from dotenv import load_dotenv
load_dotenv()
import statistics, datetime as dt
import umbrales as u

# Coeficientes de pérdidas 3.0TD (Circular 3/2020, NO Circular 7/2020)
PERDIDAS_30TD = {1: 0.166, 2: 0.175, 3: 0.165, 4: 0.165, 5: 0.138, 6: 0.180}


def precio_hora_30td_energia(t_utc, pool_price, mes_calendario=None):
    """Precio de energía de una hora: pool × (1 + pérdidas_p).

    SPOT 600 NO incluye pérdidas. Se aplican SIEMPRE multiplicando.
    Devuelve (precio_energia, periodo_3td).
    """
    local = t_utc.astimezone(u.TZ)
    mes = mes_calendario or local.month
    p3 = u.periodo_3td(local, mes)
    return pool_price * (1 + PERDIDAS_30TD[p3]), p3


def tev_base_ponderado(pool_data, ini, fin, kwh_periodos, mes_calendario):
    """TEV base (€/kWh) ponderado por kWh reales o previstos por periodo.

    TOLERANTE A HUECOS: si un periodo no tiene al menos 24 horas de datos
    (ESIOS a veces devuelve 0), se ignora y se repondera con los que sí.

    Devuelve (tev_base, info_imputacion).
    """
    horas = {p: [] for p in range(1, 7)}
    for t, v in pool_data:
        if ini <= t < fin:
            precio, p = precio_hora_30td_energia(t, v, mes_calendario)
            horas[p].append(precio)

    pesos = {p: k for p, k in kwh_periodos.items() if k > 0}
    if not pesos:
        raise ValueError("Sin kWh por periodo para ponderar TEV")

    total = suma = 0.0
    ignorados = []
    for p, w in pesos.items():
        if len(horas[p]) < 24:
            ignorados.append((p, len(horas[p])))
            continue
        media = sum(horas[p]) / len(horas[p])
        total += w * media
        suma += w

    if suma == 0:
        raise ValueError(f"Ningún periodo con horas suficientes. Ignorados: {ignorados}")

    info = {"ignorados": ignorados, "horas_disponibles": {p: len(h) for p, h in horas.items()}}
    return total / suma, info


def calibrar_fee_tev(meses_train, winsor=0.10):
    """Fee aditivo robusto sobre TEV: mediana winsorizada de residuales."""
    import numpy as np
    res = [m["tev_real"] - m["tev_base"] for m in meses_train
           if m.get("tev_real") and m.get("tev_base", 0) > 0]
    if not res:
        return 0.0
    res = np.array(res, dtype=float)
    if len(res) >= 3:
        lo, hi = np.quantile(res, [winsor, 1 - winsor])
        res = np.clip(res, lo, hi)
    return float(np.median(res))


def metricas_tev(meses, fee, columna_real="tev_real"):
    """Métricas de error del TEV con un fee dado."""
    est = [m["tev_base"] + fee for m in meses]
    real = [m[columna_real] for m in meses]
    kwh = [m.get("kwh") or 0 for m in meses]
    err = [e - r for e, r in zip(est, real)]
    pct = [100 * e / r for e, r in zip(err, real)]
    k = sum(kwh)
    return {
        "mae": statistics.mean(abs(e) for e in err),
        "mape": statistics.mean(abs(p) for p in pct),
        "sesgo": (sum(w * e for w, e in zip(kwh, err)) / k) if k else statistics.mean(err),
        "sesgo_pct": statistics.mean(pct),
        "peor_mes": max(abs(p) for p in pct),
        "n_meses": len(meses),
        "por_mes_pct": pct,
    }

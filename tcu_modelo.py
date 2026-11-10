"""Estimación del precio medio de la energía 3.0TD indexada (€/kWh, sin IVA ni impuesto eléctrico)
a partir del TCU del PVPC (coste de producción de la energía) publicado en ESIOS.

Modelos de fee disponibles:
  - calibrar_fee: aditivo, media ponderada por kWh (original).
  - calibrar_fee_robusto: aditivo, mediana winsorizada (recomendado).
  - calibrar_fee_por_bloque: multiplicativo por bloque (EXPERIMENTO, no usar en producción:
    los datos con 11-17 meses lo rechazan).
"""
from dotenv import load_dotenv
load_dotenv()
import statistics, datetime as dt

import umbrales as u

PERDIDAS_20TD = {1: 0.167, 2: 0.163, 3: 0.180}
PERDIDAS_30TD_OFICIALES = {1: 0.166, 2: 0.175, 3: 0.165, 4: 0.165, 5: 0.138, 6: 0.180}

PEAJES = u.PEAJES_30TD
CARGOS = u.CARGOS_30TD

_BLOQUES = ("P1P2", "P3P4", "P5P6")


def _bloque_de_periodo(p3):
    return "P1P2" if p3 in (1, 2) else ("P3P4" if p3 in (3, 4) else "P5P6")


def periodo_20td(hora_local):
    dia = hora_local.date()
    if dia.weekday() >= 5 or dia in u.FESTIVOS_ES:
        return 3
    h = hora_local.hour
    if h < 8:
        return 3
    if 10 <= h < 14 or 18 <= h < 22:
        return 1
    return 2


def precio_hora_30td(t_utc, tcu, incluye_perdidas, fee, mes_calendario=None,
                     perdidas_20td=None, perdidas_30td=None):
    perdidas_20td = perdidas_20td or PERDIDAS_20TD
    perdidas_30td = perdidas_30td or PERDIDAS_30TD_OFICIALES
    local = t_utc.astimezone(u.TZ)
    mes = mes_calendario or local.month
    p3 = u.periodo_3td(local, mes)
    energia = tcu
    if incluye_perdidas:
        energia = energia / (1 + perdidas_20td[periodo_20td(local)])
    energia = energia * (1 + perdidas_30td[p3])
    base = energia + PEAJES[p3] + CARGOS[p3]
    if isinstance(fee, dict):
        precio = base * (1 + fee.get(_bloque_de_periodo(p3), 0.0))
    else:
        precio = base + fee
    return precio, p3


def precio_medio_ponderado(tcu, ini, fin, incluye_perdidas, fee=0.0, kwh_periodos=None,
                           pesos=None, mes_calendario=None):
    horas = {p: [] for p in range(1, 7)}
    for t, v in tcu:
        if ini <= t < fin:
            precio, p = precio_hora_30td(t, v, incluye_perdidas, fee, mes_calendario)
            horas[p].append(precio)
    if kwh_periodos:
        pesos_p, nivel = {p: k for p, k in kwh_periodos.items() if k > 0}, "kWh facturados por periodo"
    else:
        pesos_p, nivel = {p: w for p, w in (pesos or {}).items() if w > 0}, "pesos estacionales (fallback)"
    if not pesos_p:
        raise ValueError("Faltan kWh por periodo o pesos para ponderar")
    total = suma = 0.0
    detalle = {}
    for p, w in pesos_p.items():
        if len(horas[p]) < 24:
            raise ValueError(f"Faltan precios TCU del periodo P{p} ({len(horas[p])} horas)")
        media = sum(horas[p]) / len(horas[p])
        detalle[p] = (len(horas[p]), media, w)
        total += w * media
        suma += w
    return total / suma, detalle, nivel


def _pendiente_theilsen(x, y):
    import numpy as np
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    if n < 2:
        return None
    pend = []
    for i in range(n - 1):
        dx = x[i + 1:] - x[i]
        dy = y[i + 1:] - y[i]
        mask = np.abs(dx) > 1e-9
        pend.extend((dy[mask] / dx[mask]).tolist())
    return float(np.median(pend)) if pend else None


def diagnostico_perdidas_tcu(tcu, pvpc_1001, tolerancia=0.05, min_horas=48):
    pv = dict(pvpc_1001)
    por_periodo = {1: ([], []), 2: ([], []), 3: ([], [])}
    for t, v in tcu:
        if t in pv:
            p2 = periodo_20td(t.astimezone(u.TZ))
            por_periodo[p2][0].append(v)
            por_periodo[p2][1].append(pv[t])
    pendientes = {}
    for p, (x, y) in por_periodo.items():
        if len(x) >= min_horas:
            s = _pendiente_theilsen(x, y)
            if s is not None:
                pendientes[p] = s
    if len(pendientes) < 2:
        return "PENDIENTE: datos insuficientes", pendientes
    votos_incluye = sum(abs(s - 1.0) <= tolerancia for s in pendientes.values())
    votos_no = sum(abs(s - (1 + PERDIDAS_20TD[p])) <= tolerancia
                   for p, s in pendientes.items())
    if votos_incluye > votos_no:
        return "INCLUYE_PERDIDAS", pendientes
    if votos_no > votos_incluye:
        return "NO_INCLUYE_PERDIDAS", pendientes
    return "AMBIGUO", pendientes


def calibrar_fee(meses):
    """Fee aditivo: media ponderada por kWh (original, sensible a outliers)."""
    base = sum(m["kwh"] for m in meses)
    if base <= 0:
        raise ValueError("Sin kWh para ponderar la calibración")
    return sum(m["kwh"] * (m["precio_real"] - m["precio_modelo_sin_fee"]) for m in meses) / base


def calibrar_fee_robusto(meses, winsor=0.10):
    """Fee aditivo robusto: mediana winsorizada de los residuales.

    Ignora outliers (p.ej. 2025-01, 2025-02) que desplazan la media.
    Recomendado para producción y walk-forward.
    """
    import numpy as np
    res = [m["precio_real"] - m["precio_modelo_sin_fee"] for m in meses
           if m.get("kwh", 0) > 0 and m.get("precio_modelo_sin_fee", 0) > 0]
    if not res:
        return 0.0
    res = np.array(res, dtype=float)
    if len(res) >= 3:
        lo, hi = np.quantile(res, [winsor, 1 - winsor])
        res = np.clip(res, lo, hi)
    return float(np.median(res))


def calibrar_fee_por_bloque(meses, winsor=0.05, ridge=1e-1):
    """EXPERIMENTO: fee multiplicativo por bloque con regularización fuerte.

    Los datos con 11-17 meses rechazan este modelo (MAPE 2-3x peor que el aditivo).
    Se conserva solo para comparación en backtest. NO usar en producción.
    """
    import numpy as np
    filas = []
    for m in meses:
        if not m.get("kwh_p") or m.get("precio_modelo_sin_fee", 0) <= 0:
            continue
        r = m["precio_real"] / m["precio_modelo_sin_fee"] - 1.0
        tot = sum(m["kwh_p"].values())
        if tot <= 0:
            continue
        w = {b: 0.0 for b in _BLOQUES}
        for p, k in m["kwh_p"].items():
            w[_bloque_de_periodo(int(p))] += k / tot
        filas.append(([w[b] for b in _BLOQUES], r))
    if len(filas) < 3:
        return {b: 0.0 for b in _BLOQUES}
    X = np.array([f[0] for f in filas])
    y = np.array([f[1] for f in filas])
    lo, hi = np.quantile(y, [winsor, 1 - winsor])
    y = np.clip(y, lo, hi)
    A = X.T @ X + ridge * np.eye(len(_BLOQUES))
    b = X.T @ y
    coef = np.linalg.solve(A, b)
    return dict(zip(_BLOQUES, coef.tolist()))


def metricas(meses, fee):
    if isinstance(fee, dict):
        est = []
        for m in meses:
            if "precio_modelo_con_fee_dict" in m:
                est.append(m["precio_modelo_con_fee_dict"])
            else:
                w = m.get("kwh_p") or {}
                if w:
                    b_dom = max(_BLOQUES,
                                key=lambda b: sum(k for p, k in w.items()
                                                  if _bloque_de_periodo(int(p)) == b))
                    est.append(m["precio_modelo_sin_fee"] * (1 + fee.get(b_dom, 0.0)))
                else:
                    est.append(m["precio_modelo_sin_fee"])
    else:
        est = [m["precio_modelo_sin_fee"] + fee for m in meses]
    real = [m["precio_real"] for m in meses]
    kwh = [m.get("kwh") or 0 for m in meses]
    err = [e - r for e, r in zip(est, real)]
    pct = [100 * e / r for e, r in zip(err, real)]
    k = sum(kwh)
    return {
        "mae": statistics.mean(abs(e) for e in err),
        "mape": statistics.mean(abs(p) for p in pct),
        "sesgo": (sum(w * e for w, e in zip(kwh, err)) / k) if k else statistics.mean(err),
        "sesgo_pct": statistics.mean(pct),
        "desv_tipica": statistics.stdev(err) if len(err) > 1 else 0.0,
        "por_mes_pct": pct,
        "error_euros": [e * w for e, w in zip(err, kwh)],
                               }

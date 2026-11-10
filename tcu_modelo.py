"""Estimación del coste de la energía 3.0TD con tolerancia a fallos de ESIOS."""
from dotenv import load_dotenv
load_dotenv()
import statistics, datetime as dt
import umbrales as u

PERDIDAS_20TD = {1: 0.167, 2: 0.163, 3: 0.180}
PERDIDAS_30TD_OFICIALES = {1: 0.166, 2: 0.175, 3: 0.165, 4: 0.165, 5: 0.138, 6: 0.180}
PEAJES = u.PEAJES_30TD
CARGOS = u.CARGOS_30TD

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

def precio_hora_30td(t_utc, tcu, incluye_perdidas, fee=0.0, mes_calendario=None,
                     perdidas_20td=None, perdidas_30td=None, solo_energia=False):
    perdidas_20td = perdidas_20td or PERDIDAS_20TD
    perdidas_30td = perdidas_30td or PERDIDAS_30TD_OFICIALES
    local = t_utc.astimezone(u.TZ)
    mes = mes_calendario or local.month
    p3 = u.periodo_3td(local, mes)
    energia = tcu
    if incluye_perdidas:
        energia = energia / (1 + perdidas_20td[periodo_20td(local)])
    energia = energia * (1 + perdidas_30td[p3])
    
    if isinstance(fee, dict):
        energia = energia * (1 + fee.get("P1P2" if p3 in (1,2) else ("P3P4" if p3 in (3,4) else "P5P6"), 0.0))
    else:
        energia = energia + fee

    if solo_energia:
        return energia, p3
        
    return energia + PEAJES[p3] + CARGOS[p3], p3

def precio_medio_ponderado(tcu, ini, fin, incluye_perdidas, fee=0.0, kwh_periodos=None,
                           pesos=None, mes_calendario=None, solo_energia=False):
    horas = {p: [] for p in range(1, 7)}
    for t, v in tcu:
        if ini <= t < fin:
            precio, p = precio_hora_30td(t, v, incluye_perdidas, fee, mes_calendario, solo_energia=solo_energia)
            horas[p].append(precio)
    
    if kwh_periodos:
        pesos_p = {p: k for p, k in kwh_periodos.items() if k > 0}
        nivel = "kWh facturados"
    else:
        pesos_p = {p: w for p, w in (pesos or {}).items() if w > 0}
        nivel = "pesos estacionales"
    
    if not pesos_p:
        raise ValueError("Faltan kWh por periodo o pesos para ponderar")
        
    total = suma = 0.0
    detalle = {}
    periodos_ignorados = []
    
    for p, w in pesos_p.items():
        # FALLBACK: Si un periodo no tiene suficientes horas (ej. ESIOS falló), lo ignoramos
        if len(horas[p]) < 24:
            periodos_ignorados.append((p, len(horas[p])))
            continue
            
        media = sum(horas[p]) / len(horas[p])
        detalle[p] = (len(horas[p]), media, w)
        total += w * media
        suma += w
        
    if suma == 0:
        raise ValueError(f"No hay datos válidos para ningún periodo. Ignorados: {periodos_ignorados}")
        
    # Imprimimos un aviso si hemos ignorado algún periodo, para que quede constancia
    if periodos_ignorados:
        print(f"AVISO: Periodos ignorados por falta de datos: {periodos_ignorados}. Se recalcula la ponderación con el resto.")

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
        return "PENDIENTE", pendientes
    votos_incluye = sum(abs(s - 1.0) <= tolerancia for s in pendientes.values())
    votos_no = sum(abs(s - (1 + PERDIDAS_20TD[p])) <= tolerancia for p, s in pendientes.items())
    if votos_incluye > votos_no:
        return "INCLUYE_PERDIDAS", pendientes
    if votos_no > votos_incluye:
        return "NO_INCLUYE_PERDIDAS", pendientes
    return "AMBIGUO", pendientes

def calibrar_fee(meses):
    base = sum(m["kwh"] for m in meses)
    if base <= 0:
        raise ValueError("Sin kWh para ponderar la calibración")
    return sum(m["kwh"] * (m["coste_energia_real"] - m["precio_modelo_sin_fee"]) for m in meses) / base

def calibrar_fee_robusto(meses, winsor=0.10):
    import numpy as np
    res = [m["coste_energia_real"] - m["precio_modelo_sin_fee"] for m in meses
           if m.get("kwh", 0) > 0 and m.get("precio_modelo_sin_fee", 0) > 0]
    if not res:
        return 0.0
    res = np.array(res, dtype=float)
    if len(res) >= 3:
        lo, hi = np.quantile(res, [winsor, 1 - winsor])
        res = np.clip(res, lo, hi)
    return float(np.median(res))

def metricas(meses, fee, columna_real="coste_energia_real"):
    est = [m["precio_modelo_sin_fee"] + fee for m in meses]
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
        "desv_tipica": statistics.stdev(err) if len(err) > 1 else 0.0,
        "por_mes_pct": pct,
        "error_euros": [e * w for e, w in zip(err, kwh)],
        }

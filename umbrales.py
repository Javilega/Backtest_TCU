import os, json, time, collections, datetime as dt
from zoneinfo import ZoneInfo
import requests
import holidays

TZ = ZoneInfo("Europe/Madrid")
FESTIVOS_ES = holidays.ES()

def _leer_descuento():
    texto = os.environ.get("DESCUENTO", "").strip().replace(",", ".")
    if not texto:
        return 0.425
    try:
        valor = float(texto)
    except ValueError:
        return 0.425
    if valor >= 1:
        valor /= 100
    if not 0 <= valor < 1:
        raise ValueError(f"DESCUENTO no válido: {valor}")
    return valor

DESCUENTO_FAMILIA_NUMEROSA = _leer_descuento()
FACTOR_AEROTERMIA = 0.58

PESOS_PERIODOS = {
    1:  {1: 30.85, 2: 20.59, 6: 48.56}, 2:  {1: 30.85, 2: 20.59, 6: 48.56},
    3:  {2: 30.83, 3: 20.57, 6: 48.60}, 4:  {4: 33.76, 5: 26.67, 6: 39.57},
    5:  {4: 33.76, 5: 26.67, 6: 39.57}, 6:  {3: 31.45, 4: 25.66, 6: 42.90},
    7:  {1: 31.74, 2: 25.70, 6: 42.56}, 8:  {3: 27.24, 4: 22.32, 6: 50.44},
    9:  {3: 27.24, 4: 22.32, 6: 50.44}, 10: {4: 33.80, 5: 26.70, 6: 39.51},
    11: {2: 30.82, 3: 20.59, 6: 48.59}, 12: {1: 30.85, 2: 20.59, 6: 48.56},
}

def _leer_ajuste():
    texto = os.environ.get("FACTOR_AJUSTE_30TD", "").strip().replace(",", ".")
    if not texto:
        return 1.0
    try:
        valor = float(texto)
    except ValueError:
        return 1.0
    return valor if 0.3 <= valor <= 1.5 else 1.0

FACTOR_AJUSTE_30TD = _leer_ajuste()
PEAJES_30TD = {1: 0.027511, 2: 0.012376, 3: 0.004943, 4: 0.002627, 5: 0.000111, 6: 0.000031}
CARGOS_30TD = {1: 0.035841, 2: 0.026538, 3: 0.014336, 4: 0.007168, 5: 0.004595, 6: 0.002867}
PERDIDAS_30TD = {1: 0.166, 2: 0.175, 3: 0.165, 4: 0.165, 5: 0.138, 6: 0.180}

def _leer_margen():
    texto = os.environ.get("MARGEN_COMERCIAL", "").strip().replace(",", ".")
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        return None

MARGEN_COMERCIAL = _leer_margen()
VERANO_DESDE = (5, 15)
VERANO_HASTA = (10, 15)
FACTORES_INYECCION_MENSUAL = {
    1: 0.65, 2: 0.65, 3: 0.55, 4: 0.45, 5: 0.40, 6: 0.40,
    7: 0.40, 8: 0.40, 9: 0.40, 10: 0.45, 11: 0.55, 12: 0.65,
}

def _leer_dias():
    try:
        return max(1, int(os.environ.get("DIAS_MEDIA", "").strip() or 30))
    except ValueError:
        return 30

DIAS_MEDIA = _leer_dias()
DIAS_CACHE = max(35, DIAS_MEDIA + 5)

def ahora_utc():
    return dt.datetime.now(dt.timezone.utc)

def medianoche(fecha):
    return dt.datetime.combine(fecha, dt.time(0, 0), tzinfo=TZ)

SERIES = {
    "pvpc": {"indicador": 1001, "geo": 8741, "cache": os.path.join("datos", "precios.json")},
    "spot": {"indicador": 600, "geo": 3, "cache": os.path.join("datos", "spot.json")},
}

def pedir_esios(ini, fin, serie="pvpc"):
    cfg = SERIES[serie]
    por_hora = {}
    paso = dt.timedelta(days=90)
    sub_ini = ini

    while sub_ini < fin:
        sub_fin = min(sub_ini + paso, fin)
        ultimo = None
        for intento in range(1, 4):
            try:
                r = requests.get(
                    f"https://api.esios.ree.es/indicators/{cfg['indicador']}",
                    headers={"x-api-key": os.environ["ESIOS_TOKEN"],
                             "Accept": "application/json; application/vnd.esios-api-v1+json"},
                    params={"start_date": sub_ini.isoformat(),
                            "end_date": sub_fin.isoformat(),
                            "geo_ids[]": cfg["geo"]},
                    timeout=60,
                )
                r.raise_for_status()
                for v in r.json()["indicator"]["values"]:
                    if v.get("geo_id") not in (None, cfg["geo"]):
                        continue
                    t = dt.datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
                    t = t.replace(minute=0, second=0, microsecond=0)
                    por_hora.setdefault(t, []).append(v["value"] / 1000)
                break
            except Exception as e:
                ultimo = e
                print(f"Intento {intento}/3 fallido para el tramo {sub_ini.date()} - {sub_fin.date()}: {e}")
                if intento < 3:
                    time.sleep(5)
        else:
            raise RuntimeError(f"ESIOS no responde en el tramo {sub_ini.date()} - {sub_fin.date()}: {ultimo}")
        sub_ini = sub_fin

    if not por_hora:
        raise ValueError("ESIOS no devolvió precios")
    return sorted((t, sum(vs) / len(vs)) for t, vs in por_hora.items())

def leer_cache(serie="pvpc"):
    try:
        with open(SERIES[serie]["cache"], encoding="utf-8") as f:
            crudo = json.load(f)["precios"]
        return sorted((dt.datetime.fromisoformat(k), float(v)) for k, v in crudo.items())
    except Exception:
        return []

def guardar_cache(datos, serie="pvpc"):
    ruta = SERIES[serie]["cache"]
    actual = dict(leer_cache(serie))
    actual.update(dict(datos))
    limite = ahora_utc() - dt.timedelta(days=DIAS_CACHE)
    podado = {t: p for t, p in actual.items() if t >= limite}
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"precios": {t.isoformat(): round(p, 6) for t, p in sorted(podado.items())}},
                  f, separators=(",", ":"))

def precios(ini, fin, horas_clave, serie="pvpc"):
    guardados = [(t, p) for t, p in leer_cache(serie) if ini <= t < fin]
    esperadas = int((fin - ini).total_seconds() // 3600)
    horas = {t for t, _ in guardados}
    if all(h in horas for h in horas_clave) and len(guardados) >= 0.9 * esperadas:
        return guardados, "caché"
    return pedir_esios(ini, fin, serie), "ESIOS"

def media_de(datos, fecha, dias=None):
    dias = dias or DIAS_MEDIA
    fin = medianoche(fecha)
    ini = medianoche(fecha - dt.timedelta(days=dias))
    valores = [p for t, p in datos if ini <= t < fin]
    minimo = int(dias * 24 * 0.8)
    if len(valores) < minimo:
        raise ValueError(f"Solo hay {len(valores)} precios en los {dias} días previos")
    return sum(valores) / len(valores)

def periodo_3td(hora_local, mes):
    dia = hora_local.date()
    if dia.weekday() >= 5 or dia in FESTIVOS_ES:
        return 6
    h = hora_local.hour
    if h < 8:
        return 6
    punta = (9 <= h < 14) or (18 <= h < 22)
    if mes in (1, 2, 7, 12):
        return 1 if punta else 2
    if mes in (3, 11):
        return 2 if punta else 3
    if mes in (6, 8, 9):
        return 3 if punta else 4
    return 4 if punta else 5

def es_verano(fecha):
    return VERANO_DESDE <= (fecha.month, fecha.day) <= VERANO_HASTA

def _hora_local(t):
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(TZ)

def _medias_por_periodo(serie, fecha, dias):
    fin = medianoche(fecha)
    ini = medianoche(fecha - dt.timedelta(days=dias))
    pesos = PESOS_PERIODOS[fecha.month]
    acumulado = {p: [0.0, 0] for p in pesos}
    for t, precio in serie:
        if ini <= t < fin:
            p = periodo_3td(_hora_local(t), fecha.month)
            if p in acumulado:
                acumulado[p][0] += precio
                acumulado[p][1] += 1
    return {p: (a[0] / a[1] if a[1] else 0.0, a[1]) for p, a in acumulado.items()}

def precio_aerotermia(datos, fecha, dias=None):
    texto = os.environ.get("PRECIO_AEROTERMIA", "").strip().replace(",", ".")
    if texto:
        try:
            return float(texto)
        except ValueError:
            pass
    dias = dias or DIAS_MEDIA
    pesos = PESOS_PERIODOS[fecha.month]
    medias = _medias_por_periodo(datos, fecha, dias)
    total = suma_pesos = 0.0
    for p, peso in pesos.items():
        media, n = medias[p]
        if n < 24:
            raise ValueError(f"Faltan precios del periodo P{p} para ponderar la aerotermia")
        total += peso * media
        suma_pesos += peso
    return total / suma_pesos * FACTOR_AJUSTE_30TD

def precio_aerotermia_spot(spot, fecha, dias=None, margen=None):
    margen = MARGEN_COMERCIAL if margen is None else margen
    dias = dias or DIAS_MEDIA
    pesos = PESOS_PERIODOS[fecha.month]
    medias = _medias_por_periodo(spot, fecha, dias)
    total = suma_pesos = 0.0
    detalle = {}
    for p, peso in pesos.items():
        spot_medio, n = medias[p]
        if n < 24:
            raise ValueError(f"Faltan precios spot del periodo P{p}")
        energia = spot_medio * (1 + PERDIDAS_30TD[p]) + CARGOS_30TD[p] + PEAJES_30TD[p]
        detalle[p] = (spot_medio, energia)
        total += peso * energia
        suma_pesos += peso
    return total / suma_pesos + margen, detalle

Umbrales = collections.namedtuple("Umbrales", "media inyeccion eficiencia factor modo aerotermia metodo")

def calcular_umbrales(datos, fecha, dias=None, spot=None):
    media = media_de(datos, fecha, dias)
    if es_verano(fecha):
        modo, aero, base, metodo = "verano", None, media, "media"
    else:
        modo = "invierno"
        aero, metodo = None, "pvpc"
        if isinstance(spot, list) and len(spot) > 0 and MARGEN_COMERCIAL is not None and not os.environ.get("PRECIO_AEROTERMIA", "").strip():
            try:
                aero, metodo = precio_aerotermia_spot(spot, fecha, dias)[0], "spot"
            except ValueError as e:
                print(f"AVISO: no se pudo estimar con el mercado diario ({e}); se usa el PVPC ponderado")
        if aero is None:
            aero = precio_aerotermia(datos, fecha, dias)
            if os.environ.get("PRECIO_AEROTERMIA", "").strip():
                metodo = "manual"
        base = aero
    eficiencia = base * FACTOR_AEROTERMIA / (1.0 - DESCUENTO_FAMILIA_NUMEROSA)
    factor = FACTORES_INYECCION_MENSUAL[fecha.month]
    return Umbrales(media, eficiencia * factor, eficiencia, factor, modo, aero, metodo)

def decidir(precio, umbral_inyeccion, umbral_eficiencia, precio_anterior=None, umbral_anterior=None):
    if precio <= umbral_inyeccion:
        return True, "Valle: precio por debajo del umbral de inyección"
    if precio > umbral_eficiencia:
        return False, "Precio por encima del umbral de eficiencia: apagado de seguridad"
    if (precio_anterior is not None and umbral_anterior is not None
            and precio_anterior <= umbral_anterior):
        return False, "Fin del valle: se apaga el enchufe"
    return None, "Entre ambos umbrales: no se toca el enchufe (control manual)"

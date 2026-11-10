def pedir_esios(ini, fin, serie="pvpc"):
    cfg = SERIES[serie]
    por_hora = {}
    # Reducimos el bloque a 30 días para evitar timeouts de ESIOS
    paso = dt.timedelta(days=30) 
    sub_ini = ini

    while sub_ini < fin:
        sub_fin = min(sub_ini + paso, fin)
        ultimo = None
        
        for intento in range(1, 6): # Aumentamos a 5 reintentos
            try:
                r = requests.get(
                    f"https://api.esios.ree.es/indicators/{cfg['indicador']}",
                    headers={"x-api-key": os.environ["ESIOS_TOKEN"],
                             "Accept": "application/json; application/vnd.esios-api-v1+json"},
                    params={"start_date": sub_ini.isoformat(),
                            "end_date": sub_fin.isoformat(),
                            "geo_ids[]": cfg["geo"]},
                    timeout=90, # Más tiempo de espera
                )
                r.raise_for_status()
                # Si la respuesta está vacía, forzamos un error para reintentar
                if not r.json().get("indicator", {}).get("values"):
                    raise ValueError("ESIOS devolvió una respuesta vacía")
                    
                for v in r.json()["indicator"]["values"]:
                    if v.get("geo_id") not in (None, cfg["geo"]):
                        continue
                    t = dt.datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
                    t = t.replace(minute=0, second=0, microsecond=0)
                    por_hora.setdefault(t, []).append(v["value"] / 1000)
                break # Éxito
            except Exception as e:
                ultimo = e
                print(f"Intento {intento}/5 fallido para {sub_ini.date()} - {sub_fin.date()}: {e}")
                time.sleep(10) # Esperamos 10 segundos entre reintentos
        else:
            raise RuntimeError(f"ESIOS no responde tras 5 intentos en {sub_ini.date()} - {sub_fin.date()}: {ultimo}")
            
        sub_ini = sub_fin

    if not por_hora:
        raise ValueError("ESIOS no devolvió precios")
    return sorted((t, sum(vs) / len(vs)) for t, vs in por_hora.items())

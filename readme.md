# Backtest TCU-PVPC

Modelo de estimación del precio medio de energía 3.0TD indexada a partir del TCU del PVPC (ESIOS 1001) y del Mercado Diario (ESIOS 600).

## Modelo de producción

- **Fee aditivo robusto** (mediana winsorizada al 10%): ignora outliers que desplazan la media.
- **Walk-forward con ventana móvil de 6 meses**: recalibra con el régimen reciente.
- **Sin ancla**: el ancla a media móvil 3m empeora el MAPE en ambos indicadores.
- **2026-04 excluido del entrenamiento**: no tiene desglose por periodos.

### Resultados walk-forward (17 meses, ventana 6, train hasta 2026-03)

| Indicador | MAPE | Sesgo | Peor mes |
|---|---|---|---|
| SPOT (600) | **5,20%** | −0,98% | 13,5% |
| PVPC (1001) | **11,98%** | +0,15% | 23,0% |

SPOT es apto para producción. PVPC requiere revisión: el diagnóstico trivial `INCLUYE_PERDIDAS` (pendientes 1,000) está forzando `incluye=True`; con `incluye=False` se espera mejora (pendiente de validar).

### Modelos descartados

- **Fee por bloque** (multiplicativo por P1P2/P3P4/P5P6): MAPE 2-3× peor que el aditivo. Los datos con 11-17 meses lo rechazan por sobreajuste.
- **Ancla a media móvil 3m**: empeora el MAPE en SPOT (5,20% → 9,34%) y en PVPC (11,98% → 14,37%).

## Instalación local

```bash
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tu token de ESIOS
python backtest_tcu.py facturas_actualizadas.csv --train-hasta 2026-03 --ventana 6

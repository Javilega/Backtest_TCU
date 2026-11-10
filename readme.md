# Backtest TCU-PVPC

Modelo de estimación del precio medio de energía 3.0TD indexada a partir del TCU del PVPC (ESIOS 1001) y del Mercado Diario (ESIOS 600).

## Instalación local

```bash
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tu token de ESIOS
python backtest_tcu.py facturas_actualizadas.csv --train-hasta 2026-03
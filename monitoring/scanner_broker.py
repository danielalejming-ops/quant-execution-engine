import time
import requests
import os
import sys
import logging
import datetime
from dotenv import load_dotenv

# Forzar codificación UTF-8 en Windows para evitar crashes por Emojis en consola
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import trader_engine_v4_aws as trader_engine
import mock_executor as broker_engine
from risk_guardian import get_guardian

load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_USER_ID = 922218985

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE PARES
# BNB-USD desactivado temporalmente (WR=37.3%, peor símbolo, 50% del costo)
# Se reactivará cuando WR > 45% por 7 días consecutivos.
# ─────────────────────────────────────────────────────────────────────────────
SYMBOLS = ["BTC-USD", "ETH-USD"]
# SYMBOLS = ["BTC-USD", "ETH-USD", "BNB-USD"]  # ← Re-activar en Fase 4

# ADX mínimo global de respaldo (fallback si settings_v2.json no tiene datos del símbolo)
# El engine usa los umbrales POR SÍMBOLO de settings_v2.json (Optuna los optimiza).
# BTC-USD → ADX > 15 | ETH-USD → ADX > 17  (valores genéticos actuales de Optuna)
ADX_THRESHOLD_FALLBACK = 25  # Solo se usa si el símbolo no está en settings_v2.json

# Win rates actuales (actualizados desde settings.json)
SYMBOL_WIN_RATES = {
    "BTC-USD": 0.417,
    "ETH-USD": 0.384,
    "BNB-USD": 0.373,
}

def enviar_alerta_telegram(mensaje, parse_mode="HTML"):
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN no configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": ALLOWED_USER_ID, "text": mensaje, "parse_mode": parse_mode}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Error enviando alerta Telegram: {e}")


def _formatear_estado_guardian(guardian) -> str:
    """Genera un mini-reporte del RiskGuardian para logs y Telegram."""
    rpt = guardian.get_status_report()
    trauma_str = ", ".join(rpt["trauma_symbols"]) if rpt["trauma_symbols"] else "Ninguno"
    pos_str = ", ".join(rpt["open_positions"]) if rpt["open_positions"] else "Ninguna"
    dead_zone = "⚠️ SÍ" if rpt["dead_zone_active"] else "✅ No"
    return (
        f"━━━━ 🛡️ RISK GUARDIAN ━━━━\n"
        f"🔌 Circuit Breaker: {rpt['circuit_breaker']}\n"
        f"💸 Comisión Hoy: ${rpt['daily_commission_usd']:.2f} / ${rpt['daily_limit_usd']:.2f} USDT\n"
        f"📊 Posiciones Abiertas: {pos_str}\n"
        f"🏆 Racha Ganadora: {rpt['win_streak']} trades\n"
        f"🩹 Símbolos en Trauma: {trauma_str}\n"
        f"😴 Zona Muerta Activa: {dead_zone} ({rpt['hora_utc']} UTC)"
    )


def escanear_mercado():
    print("🚀 QUANT v2.0 — Iniciando Scanner con Risk Guardian activo...")
    print(f"   Pares activos: {SYMBOLS}")
    print(f"   ADX umbral genético: Por-Símbolo (Optuna) | Fallback: {ADX_THRESHOLD_FALLBACK}")
    print(f"   R/R mínimo: 3:1 (TP=2.4% / SL=0.8%)")
    print(f"   Circuit Breaker: $20 USDT/día")
    print(f"   Sizing: Quarter Kelly (5% del capital)")
    print()

    # Apagamos Gemini para el análisis puro chartista/estadístico de 1 hora
    trader_engine.GEMINI_API_KEY = None

    guardian = get_guardian()

    # Leer umbrales genééticos por símbolo de Optuna
    def _get_adx_threshold(sym):
        try:
            import json
            if os.path.exists("settings_v2.json"):
                with open("settings_v2.json", "r") as f:
                    s = json.load(f)
                if sym in s:
                    return s[sym].get("ADX_THRESH", ADX_THRESHOLD_FALLBACK)
        except Exception:
            pass
        return ADX_THRESHOLD_FALLBACK

    # Enviar reporte inicial al iniciar
    startup_msg = (
        f"⚡ <b>QUANT v2.0 INICIADO</b> ⚡\n\n"
        f"🔄 Motor reiniciado con Risk Guardian v1.0\n"
        f"📊 Pares: {', '.join(SYMBOLS)}\n"
        f"📐 ADX Umbral: Por-Símbolo (Genético vía Optuna) | Fallback: {ADX_THRESHOLD_FALLBACK}\n"
        f"⚖️ R/R Mínimo: 3:1\n"
        f"🛑 Circuit Breaker: $20 USDT/día\n\n"
        f"{_formatear_estado_guardian(guardian)}"
    )
    enviar_alerta_telegram(startup_msg)

    while True:
        print("\n" + "="*50)
        now_utc = datetime.datetime.utcnow()
        print(f"🔍 NUEVO CICLO — {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print("="*50)

        # ── Verificar circuit breaker ANTES de escanear ──
        status = guardian.get_status_report()
        if status["circuit_breaker"] != "✅ Normal":
            print(f"⛔ {status['circuit_breaker']} — Saltando ciclo completo.")
            logger.warning("Circuit breaker activo. Esperando próxima vela.")
            _sleep_hasta_proxima_vela()
            continue

        # ── Verificar zona muerta ──
        hora_utc = now_utc.hour
        if 0 <= hora_utc < 4:
            print(f"😴 Zona Muerta UTC ({hora_utc}:xx). Sin trades hasta las 04:00 UTC.")
            _sleep_hasta_proxima_vela()
            continue

        trades_ejecutados = 0

        for sym in SYMBOLS:
            print(f"\nAnalizando {sym}...", end=" ")
            try:
                report = trader_engine.analizar_mercado(sym, "1h")
                if not report or "Signal" not in report:
                    print("Sin datos o error en engine.")
                    continue

                signal   = report.get("Signal", "WAIT")
                adx      = report.get("ADX", 0)
                precio   = report.get("Precio Actual", 0)
                win_rate = SYMBOL_WIN_RATES.get(sym, 0.40)
                adx_thresh = _get_adx_threshold(sym)  # Umbral genéético por símbolo

                print(f"[{signal}] ADX={adx:.1f} (umbral genéético={adx_thresh})", end="")

                # El engine ya aplicó el filtro ADX internamente con el umbral correcto.
                # Este bloque solo bloqueamos si la señal no es de acción.
                if signal not in ["COMPRA", "VENTA"]:
                    print(f" → ESPERA (señal={signal})")
                    continue

                print(f" ✅ Señal válida")

                # ── Extraer TP/SL del report ──
                key = "Long" if signal == "COMPRA" else "Short"
                dicc_signal = report.get(key, {})
                tp = dicc_signal.get("TP", 0)
                sl = dicc_signal.get("SL", 0)

                if tp <= 0 or sl <= 0:
                    print(f"   ⚠️ TP/SL inválidos (tp={tp}, sl={sl}). Saltando.")
                    continue

                # ── RISK GUARDIAN: Verificar permiso de trade ──
                ok, razon = guardian.can_trade(
                    symbol=sym,
                    tp=tp,
                    sl=sl,
                    signal=signal,
                    entry_price=precio
                )

                if not ok:
                    print(f"   🛡️ BLOQUEADO por Guardian: {razon}")
                    logger.info(f"[Guardian] {sym} bloqueado: {razon}")
                    continue

                # ── Calcular sizing con Quarter Kelly ──
                try:
                    billetera = broker_engine.obtener_balance_usdt() if hasattr(broker_engine, "obtener_balance_usdt") else 3189.23
                    if billetera <= 0:
                        billetera = 3189.23
                except:
                    billetera = 3189.23

                monto_riesgo_usd = guardian.get_kelly_size(billetera, sym, win_rate)

                # ── EJECUTAR TRADE ──
                emoji = "🟢 LONG" if signal == "COMPRA" else "🔴 SHORT"
                print(f"   ➡️ Ejecutando {emoji} | TP:{tp:.2f} SL:{sl:.2f} | Risk:${monto_riesgo_usd:.2f}")
                
                estado_rl = report.get("RL_State", "UNKNOWN")
                motivo_ml = report.get("SMC_Micro", "Estructura técnica institucional.")

                res = broker_engine.ejecutar_trade_oco_testnet(
                    sym, signal,
                    sl=float(sl),
                    tp=float(tp),
                    rl_state=estado_rl
                )

                if res.get("estado") == "success":
                    # Registrar apertura en guardian
                    guardian.register_trade_open(sym, signal, precio)

                    # Calcular R/R real para el reporte
                    if signal == "COMPRA":
                        rr_real = (tp - precio) / max(precio - sl, 1e-9)
                    else:
                        rr_real = (precio - tp) / max(sl - precio, 1e-9)

                    mensaje = (
                        f"⚡ <b>QUANT v2.0 — FRANCOTIRADOR DISPARADO</b> ⚡\n\n"
                        f"Activo: <b>{sym}</b> | TF: 1h\n"
                        f"Dirección: {emoji}\n"
                        f"ADX: <code>{adx:.2f}</code> (Tendencia Confirmada ✅)\n"
                        f"Precio de Entrada: <code>${precio:,.4f}</code>\n"
                        f"Take Profit: <code>${tp:,.4f}</code>\n"
                        f"Stop Loss: <code>${sl:,.4f}</code>\n"
                        f"<b>R/R Real: {rr_real:.2f}:1</b> ✅\n"
                        f"Riesgo: <code>${monto_riesgo_usd:.2f} USDT (Kelly 5%)</code>\n\n"
                        f"🤖 <b>AUTO-EJECUCIÓN PAPER TRADING OK</b>\n"
                        f"Qty: <code>{res.get('qty')}</code>\n"
                        f"Orden ID: <code>{res.get('market_id')}</code>\n\n"
                        f"📝 Análisis: {motivo_ml}\n\n"
                        f"{_formatear_estado_guardian(guardian)}"
                    )
                    enviar_alerta_telegram(mensaje)
                    trades_ejecutados += 1
                    print(f"   ✅ Trade ejecutado. Qty: {res.get('qty')} | ID: {res.get('market_id')}")

                else:
                    msg_err = res.get("msg", "Error desconocido")
                    print(f"   ❌ Broker rechazó: {msg_err}")
                    enviar_alerta_telegram(
                        f"⚠️ <b>QUANT v2.0 — RECHAZO BROKER</b>\n"
                        f"Activo: {sym} | Señal: {emoji}\n"
                        f"Error: <code>{msg_err}</code>"
                    )

            except Exception as e:
                logger.error(f"Error analizando {sym}: {e}", exc_info=True)
                print(f"   ⚠️ Excepción: {e}")

        print(f"\n✅ Ciclo completo. {trades_ejecutados} trade(s) ejecutado(s).")
        print(_formatear_estado_guardian(guardian).replace("\n", " | "))

        _sleep_hasta_proxima_vela()


def _sleep_hasta_proxima_vela():
    """Duerme exactamente hasta el cierre de la próxima vela de 1h."""
    ahora = datetime.datetime.now()
    siguiente_hora = (ahora.replace(minute=0, second=0, microsecond=0)
                      + datetime.timedelta(hours=1))
    segundos = (siguiente_hora - ahora).total_seconds()
    print(f"\n⏳ Durmiendo {segundos:.0f}s hasta la próxima vela ({siguiente_hora.strftime('%H:%M:%S')})...")
    time.sleep(segundos)


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("Error: falta TELEGRAM_TOKEN en .env")
        sys.exit(1)

    try:
        escanear_mercado()
    except KeyboardInterrupt:
        print("\n🔴 Quant v2.0 detenido por el usuario.")

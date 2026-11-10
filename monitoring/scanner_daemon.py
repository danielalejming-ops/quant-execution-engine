import time
import requests
import os
import sys
import logging
from dotenv import load_dotenv

# Reutilizar el motor principal y el banco virtual
import trader_engine_v3 as trader_engine
import paper_trader
import broker_engine
import mpt_engine
import datetime
import threading
from nightly_reflection import reflexionar
from scaler_engine import calcular_conviccion

# Cargar variables de entorno
load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_USER_ID = 922218985
LIVE_MODE = os.environ.get("LIVE_MODE", "False").lower() == "true"

logger = logging.getLogger(__name__)

# Top 10 High Volume Altcoins en Binance (Actualizado)
SYMBOLS = ["ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "AVAX-USD", "ADA-USD", "DOGE-USD", "LINK-USD", "POL-USD", "DOT-USD"]

def enviar_alerta_telegram(mensaje, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": ALLOWED_USER_ID, "text": mensaje, "parse_mode": parse_mode}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Error enviando alerta: {e}")

def escanear_mercado():
    print("🚀 Iniciando Escáner Centinela (Altcoins) - Buscando Setups de Alta Probabilidad...")
    
    # Deshabilitamos temporalmente Gemini para el escaneo para ahorrar tokens/tiempo.
    # Queremos análisis puro quant.
    import mpt_engine
    import liquidation_engine
    import threading
    
    # 0. ENCENDER RADAR DE CASCADAS (Hilo Secundario Websocket)
    sims_usdt = [s.replace("-USD", "USDT") for s in SYMBOLS]
    escudo = liquidation_engine.LiquidationCatcher(simbolos=sims_usdt)
    hilo_liquidaciones = threading.Thread(target=escudo.encender_radares, daemon=True)
    hilo_liquidaciones.start()
    
    # 1. OPTIMIZACIÓN MARKOWITZ INICIAL (Se calcula 1 vez al día de forma autónoma)
    pesos_mpt = mpt_engine.optimizar_frontera_markovitz(SYMBOLS)
    print("\n[🛡️ GUARDIA MARKOWITZ V40 ACTIVADA]")
    for s, p in pesos_mpt.items():
        print(f" > {s}: {p*100:.1f}%")
        
    ciclos = 0
    while True:
        ciclos += 1
        if ciclos % 96 == 0:  # Cada 96 ciclos de 15 min = 24 horas
            pesos_mpt = mpt_engine.optimizar_frontera_markovitz(SYMBOLS)
            logger.info("Portfolio rebalanceado vía MPT (24h).")
            
        print("\n" + "="*40)
        print("🔍 NUEVO CICLO V40: APEX ESCÁNER (15m/1h)")
        print("="*40)
        
        atracciones_encontradas = 0
        
        for sim in SYMBOLS:
            sim_puro = sim.replace("-USD", "USDT")
            if pesos_mpt.get(sim_puro, 0) < 0.01:
                print(f"🛑 Ignorando {sim} -> Excluido por Markowitz (Toxic Drawdown).")
                continue
                
            print(f"Analizando {sim}...", end=" ")
            try:
                report = trader_engine.analizar_mercado(sim, "1h", use_gemini=False)
                if not report or "Signal" not in report: 
                    print("Error/Sin data.")
                    continue
                    
                # Evaluar fuerza del setup
                signal = report.get("Signal", "WAIT")
                adx = report.get("ADX", 0)
                
                print(f"[{signal}] (ADX: {adx:.1f})")
                
                # Criterio Estricto: ADX > 25 (Tendencia fuerte confirmada) y una Señal Clara
                if signal in ["LONG", "SHORT"] and adx > 25:
                    emoji = "🟢 ALCISTA" if signal == "LONG" else "🔴 BAJISTA"
                    
                    # Extraer TP y SL del reporte principal de Quant
                    dicc_signal = report.get(signal.capitalize(), {})
                    tp = dicc_signal.get('Take_Profit', 0)
                    sl = dicc_signal.get('Stop_Loss', 0)
                    kelly_risk = report.get('Kelly_Risk_Pct', 0.02)
                    
                    trade_info = ""
                    # CALCULADOR DE CONVICCIÓN DINÁMICO (V41: NLP + OnChain + Tech)
                    score_conviccion, kelly_riesgo_dinamico = calcular_conviccion(sim, signal, adx)
                    print(f"[{sim}] Puntuación V41: {score_conviccion}/10. Riesgo ajustado a: {kelly_riesgo_dinamico*100}%")
                    
                    # Lanzar ejecución autónoma contable en el Paper Trader o Bybit
                    if tp > 0 and sl > 0:
                        if LIVE_MODE:
                            trade = broker_engine.ejecutar_trade_bybit(
                                simbolo=sim,
                                side=signal,
                                sl=float(sl),
                                tp=float(tp),
                                rl_state="UNKNOWN",
                                kelly_pct=kelly_riesgo_dinamico
                            )
                            if trade and trade.get("estado") == "success":
                                paper_trader.insert_live_position(trade)
                                trade_info = (
                                    f"\n🤖 <b>AUTO-EJECUCIÓN BYBIT V5 (REAL) Activada</b>\n"
                                    f"Mercado: <code>{trade['sym']}</code>\n"
                                    f"Gestión de Riesgo Dinámico (Convicción {score_conviccion}/10): <code>{kelly_riesgo_dinamico*100:.2f}%</code>\n"
                                )
                            else:
                                trade_info = f"\n❌ Bybit rechazó la orden: {trade.get('msg')}"
                        else:
                            trade = paper_trader.open_position(
                                simbolo=sim,
                                side=signal,
                                current_price=report['Current_Price'],
                                sl=float(sl),
                                tp=float(tp),
                                risk_pct=kelly_riesgo_dinamico
                            )
                            if trade:
                                trade_info = (
                                    f"\n🤖 <b>AUTO-EJECUCIÓN PAPER TRADING Activada</b>\n"
                                    f"Capital Asignado: <code>${trade['capital_used']:.2f}</code>\n"
                                    f"Riesgo Controlado Dinámico (Score {score_conviccion}): <code>${trade['risk_amount_usd']:.2f}</code>\n"
                                )
                            
                    mensaje = (
                        f"⚡ <b>CENTINELA ALTCOIN: Setup Multi-Confluencia Detectado</b> ⚡\n\n"
                        f"Activo: <b>{sim}</b> (1h)\n"
                        f"Dirección: {emoji}\n"
                        f"Fuerza de Tendencia (ADX): <code>{adx:.2f}</code>\n"
                        f"Precio Actual: <code>${report['Current_Price']:.4f}</code>\n"
                        f"{trade_info}"
                        f"\nRevisa tu terminal enviando: <code>/get_signal {sim} 1h</code> para el reporte gráfico o revisa tu <code>/portfolio</code>."
                    )
                    enviar_alerta_telegram(mensaje)
                    atracciones_encontradas += 1
                    
            except Exception as e:
                print(f"Falsa alarma ({e})")
                
        print(f"Ciclo terminado. {atracciones_encontradas} oportunidades sólidas encontradas.")
        print("Durmiendo por 15 minutos hasta la próxima revisión táctica...")
        
        # Dormir 15 minutos para cazar cierres de vela Scalper sin descuidar el Swing
        time.sleep(900)

def daemon_reflexion():
    print("Iniciando cron-job de Reflexión Nocturna (03:00 AM)...")
    last_run_day = None
    while True:
        now = datetime.datetime.now()
        if now.hour == 3 and now.day != last_run_day:
            try:
                print("Ejecutando Reflexión Nocturna MPT/Cognitiva...")
                reflexionar()
                last_run_day = now.day
            except Exception as e:
                print("Fallo en la reflexión profunda:", e)
        time.sleep(1800) # Dormir media hora

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("Error: falta TELEGRAM_TOKEN")
        sys.exit(1)
        
    try:
        # Arrancar hilo de memoria nocturna en background
        hilo_noc = threading.Thread(target=daemon_reflexion, daemon=True)
        hilo_noc.start()
        
        escanear_mercado()
    except KeyboardInterrupt:
        print("\nSistemas Centinela Apagados por el Usuario.")

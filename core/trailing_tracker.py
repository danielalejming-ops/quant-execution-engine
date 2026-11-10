import os
import sys
import time
import json
import logging
import requests
from dotenv import load_dotenv

from pybit.unified_trading import HTTP
from rl_agent_v2 import rl_engine
import broker_engine
import paper_trader

load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_USER_ID = 922218985

API_KEY = os.environ.get("BYBIT_API_KEY")
API_SECRET = os.environ.get("BYBIT_SECRET")

try:
    client = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
except Exception as e:
    print(f"Error initializing Bybit Client: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def enviar_alerta_telegram(mensaje, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": ALLOWED_USER_ID, "text": mensaje, "parse_mode": parse_mode}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Error enviando alerta: {e}")

def enviar_pnl_telegram(sym, dictamen, entry_price, fill_price, realized_pnl):
    emoji_pnl = "🟩" if realized_pnl > 0 else "🟥"
    entry_str = f"${entry_price:.4f}" if entry_price else "N/A"
    fill_str = f"${fill_price:.4f}" if fill_price else "N/A"
    
    mensaje = (
        f"🔔 <b>REPORTE OCO CERRADO (BYBIT MAINNET)</b> 🔔\n\n"
        f"Activo: <b>{sym}</b>\n"
        f"Dictamen: <b>{dictamen}</b>\n\n"
        f"Entrada: <code>{entry_str}</code>\n"
        f"Salida: <code>{fill_str}</code>\n"
        f"Liquidación Total: {emoji_pnl} <b>${realized_pnl:.2f} USDT</b>\n\n"
        f"<i>(Operación auditada nativamente en Bybit UTA)</i>"
    )
    enviar_alerta_telegram(mensaje)

ALERTA_ENVIADA = {}

def rastrear_trades_oco_live():
    print("🕵️ Iniciando Trailing Tracker Live - Patrullando Bybit V5...")
    ultima_lectura = 0
    
    while True:
        ahora = time.time()
        if ahora - ultima_lectura > 10:
            ultima_lectura = ahora
            try:
                pos_res = client.get_positions(category="linear", settleCoin="USDT")
                if pos_res.get("retCode") != 0:
                    time.sleep(2)
                    continue
                    
                posiciones_abiertas = [p for p in pos_res["result"]["list"] if float(p.get("size", 0)) > 0]
            except Exception as e:
                logger.error(f"Error consultando Bybit API: {e}")
                posiciones_abiertas = []

            if not posiciones_abiertas:
                time.sleep(1)
                continue
            
            for pos in posiciones_abiertas:
                sym = pos.get('symbol')
                mark_price = float(pos.get('markPrice', 0))
                entry_price = float(pos.get('avgPrice', 0))
                side_str = pos.get('side', '') # "Buy" or "Sell"
                
                if mark_price == 0 or entry_price == 0: continue
                
                try:
                    profit_pct = 0.0
                    if side_str == "Buy": profit_pct = (mark_price - entry_price) / entry_price * 100
                    else: profit_pct = (entry_price - mark_price) / entry_price * 100
                    
                    current_sl = float(pos.get("stopLoss", 0) or 0)
                    active_ts = float(pos.get("trailingStop", 0) or 0)
                    
                    # Trailing Logic
                    # TP1 = 17.5% ROI -> 1.75% Precio Base (Apalancamiento 10x asimilado)
                    if profit_pct >= 2.75:
                        p_tp1 = entry_price * 1.0175 if side_str == "Buy" else entry_price * 0.9825
                        sl_needs_update = False
                        if side_str == "Buy" and current_sl < p_tp1: sl_needs_update = True
                        elif side_str == "Sell" and (current_sl > p_tp1 or current_sl == 0): sl_needs_update = True
                        
                        ts_distance = round(entry_price * 0.010, 4)
                        
                        if sl_needs_update or active_ts == 0.0:
                            p_tp1_str = str(round(p_tp1, 4))
                            dist_str = str(ts_distance)
                            res_sl = client.set_trading_stop(
                                category="linear",
                                symbol=sym,
                                stopLoss=p_tp1_str,
                                trailingStop=dist_str,
                                positionIdx=pos.get("positionIdx", 0)
                            )
                            if res_sl.get("retCode") == 0:
                                logger.info(f"[{sym}] Activando Trailing Nivel 3. Profit: {profit_pct:.2f}%")
                                msg_id = f"{sym}_TP2"
                                if sl_needs_update and ALERTA_ENVIADA.get(msg_id, 0) < time.time() - 86400:
                                    enviar_alerta_telegram(f"🛡️ <b>ESCALÓN TP2 CONQUISTADO: {sym}</b>\n\nAvance: +{profit_pct:.2f}%\nStop Loss Movido a TP1 (+17.5% ROI asegurado): <code>${p_tp1_str}</code>\n🚀 <b>Dron Trailing Stop Activado Automáticamente.</b>")
                                    ALERTA_ENVIADA[msg_id] = time.time()

                    elif profit_pct >= 1.75:
                        diff_sl_entry = abs(current_sl - entry_price)
                        if diff_sl_entry > (entry_price * 0.001):
                            ep_str = str(round(entry_price, 4))
                            res_sl = client.set_trading_stop(
                                category="linear",
                                symbol=sym,
                                stopLoss=ep_str,
                                takeProfit=pos.get("takeProfit", ""),
                                tpslMode="Full",
                                positionIdx=pos.get("positionIdx", 0)
                            )
                            if res_sl.get("retCode") == 0:
                                logger.info(f"[{sym}] Activando Breakeven Bybit. Profit: {profit_pct:.2f}%")
                                msg_id = f"{sym}_TP1"
                                if ALERTA_ENVIADA.get(msg_id, 0) < time.time() - 86400:
                                    enviar_alerta_telegram(f"🛡️ <b>ESCALÓN TP1 CONQUISTADO (BREAKEVEN): {sym}</b>\n\nAvance: +{profit_pct:.2f}%\nStop Loss Auto-Movido a Entrada: <code>${ep_str}</code>")
                                    ALERTA_ENVIADA[msg_id] = time.time()
                                
                except Exception as e:
                    logger.error(f"Falla rastreando posicion {sym}: {e}")

if __name__ == "__main__":
    rastrear_trades_oco_live()

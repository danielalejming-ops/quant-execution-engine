import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, Application
from dotenv import load_dotenv
from trader_engine_v4 import analizar_mercado, chatear_con_ia
import asyncio
import chart_generator
import arbitrage_engine_v2
import hft_tape_reader
import profit_sweeper
import omni_scanner_daemon
import trailing_tracker
import time
from pybit.unified_trading import HTTP

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN_V2", "REEMPLAZA_CON_TU_TOKEN")
ALLOWED_USER_ID = 922218985

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    user = update.effective_user
    await update.message.reply_html(
        rf"¡Hola {user.mention_html()}! Soy tu Analista Quant Pro. 📈"
        "\n\nActivos Soportados:"
        "\n• <b>Cripto:</b> /get_signal BTC-USD (o BTCUSDT)"
        "\n• <b>Forex:</b> /get_signal EURUSD"
        "\n• <b>Futuros:</b> /get_signal ES=F"
        "\n• <b>Acciones:</b> /get_signal AAPL"
        "\n\n¡Estoy conectado a la IA Generativa Avanzada para darte reportes cualitativos!"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text("✅ Sistemas operativos. Los motores de IA y Análisis Cuantitativo están encendidos.")

async def get_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if not context.args:
        await update.message.reply_text("⚠️ Por favor proporciona un símbolo. Ejemplo: /get_signal BTC-USD 1h")
        return
    
    simbolo = context.args[0].upper()
    intervalo = "1d"
    if len(context.args) > 1:
        intervalo = context.args[1].lower()

    await update.message.reply_text(f"🔍 Sintetizando Reporte Quant y Consultando la IA de Trading para {simbolo} ({intervalo})...")
    
    # Obtener el reporte del engine
    report = analizar_mercado(simbolo, intervalo)
    
    if "Error" in report:
        await update.message.reply_text(f"❌ Ocurrió un error: {report['Error']}")
        return

    signal_emoji = "🟢" if report["Signal"] == "COMPRA" else "🔴" if report["Signal"] == "VENTA" else "🟡"
    
    mensaje = (
        f"📊 <b>Reporte Profesional: {report['Simbolo Original']} ({report['Simbolo']} - Velas {report.get('Intervalo', '1d')})</b>\n\n"
        
        f"⚙️ <b>Visión Técnica (Cuantitativa):</b>\n"
        f"Señal Matemática: {signal_emoji} <b>{report['Signal']}</b>\n"
        f"Precio de Mercado: <code>{report['Precio Actual']}</code>\n"
        f"EMA {report.get('EMA Short Period', 9)}: <code>{report.get('EMA Short', 'N/A')}</code> | EMA {report.get('EMA Long Period', 21)}: <code>{report.get('EMA Long', 'N/A')}</code>\n"
        f"ADX (Fuerza): <code>{report['ADX']}</code> | RSI: <code>{report.get('RSI', 'N/A')}</code>\n"
        f"ATR (Volatilidad): <code>{report.get('ATR', 'N/A')}</code>"
        f"{' | VWAP: <code>' + str(report['VWAP']) + '</code>' if 'VWAP' in report else ''}\n"
        f"Régimen (Algorítmico): <b>{report.get('Regimen_Mercado', 'N/A')}</b>\n"
    )
    
    if "Titulares_Macro" in report and report["Titulares_Macro"] != "Titulares no disponibles." and report["Titulares_Macro"] != "No se encontraron noticias macro-económicas recientes.":
        mensaje += f"\n📰 <b>Oráculo de Noticias Fundamentales (Última Hora):</b>\n<i>{report['Titulares_Macro']}</i>\n"
        
    if "Fear_And_Greed" in report or "Funding_Rate" in report or "Long_Short_Ratio" in report:
        mensaje += f"\n🧭 <b>Sentimiento y Apalancamiento:</b>\n"
        if "Fear_And_Greed" in report:
            mensaje += f"Fear & Greed Index: <code>{report['Fear_And_Greed']}</code>\n"
        if "Funding_Rate" in report:
            mensaje += f"Funding Rate (Futuros): <code>{report['Funding_Rate']}</code>\n"
        if "Long_Short_Ratio" in report:
            ls = report['Long_Short_Ratio']
            mensaje += f"Top L/S Ratio: <code>{ls['Ratio']:.2f}</code> (🟢 {ls['Long_Pct']:.1f}% L vs 🔴 {ls['Short_Pct']:.1f}% S)\n"

    if "OrderBook" in report:
        ob = report["OrderBook"]
        cvd_text = f"CVD (Delta Acumulado): {'🟢' if ob['CVD'] > 0 else '🔴'} <code>{ob['CVD']}</code>\n" if "CVD" in ob else ""
        mensaje += (
            f"\n🌐 <b>Macro Order Book ({ob.get('Exchanges_Agregados', 5)} Exchanges):</b>\n"
            f"Presión de Compra: 🟢 <code>{ob['Presion_Compra']}</code>\n"
            f"Presión de Venta:  🔴 <code>{ob['Presion_Venta']}</code>\n"
            f"{cvd_text}"
            f"Muro de Compra 🧱: <code>{ob['Muro_Compra']['Precio']}</code> (Vol: {ob['Muro_Compra']['Volumen']})\n"
            f"Muro de Venta 🧱: <code>{ob['Muro_Venta']['Precio']}</code> (Vol: {ob['Muro_Venta']['Volumen']})\n"
        )

    if "Footprint" in report:
        fp = report["Footprint"]
        fp_emoji = "🟢 Agresión Compra" if fp['POC_Delta'] > 0 else "🔴 Agresión Venta" if fp['POC_Delta'] < 0 else "⚪ Neutral"
        mensaje += (
            f"\n👣 <b>Order Flow (Footprint - 1k Trades):</b>\n"
            f"POC (Punto de Control): <code>${fp['POC_Price']}</code>\n"
            f"Volumen en POC: <code>{fp['POC_Total_Vol']}</code>\n"
            f"Delta en POC: {fp_emoji} (<code>{fp['POC_Delta']}</code>)\n"
        )
        
    if "ML_Prediction" in report:
        ml = report["ML_Prediction"]
        mensaje += (
            f"\n🧠 <b>Motor Predictivo ML (Random Forest):</b>\n"
            f"Veredicto IA Estadístico: <b>{ml['Clasificacion']}</b>\n"
            f"Probabilidad Alcista: <code>{ml['Probabilidad_Alcista']:.2f}%</code>\n"
            f"Probabilidad Bajista: <code>{ml['Probabilidad_Bajista']:.2f}%</code>\n"
            f"Sensor Principal: Peso mayor en <code>{ml['Top_Feature']}</code>\n"
            f"⚖️ Recomendación de Riesgo (Kelly Criterion): <b>{report.get('Kelly_Risk_Pct', 0.02)*100:.2f}%</b> del Portafolio\n"
        )
        
    if "Social_Sentinel" in report and report["Social_Sentinel"]:
        sn = report["Social_Sentinel"]
        sn_emoji = "🔥 FOMO" if sn['Score'] > 5 else "🥶 PÁNICO (FUD)" if sn['Score'] < -5 else "🥱 NEUTRAL"
        mensaje += (
            f"\n🎭 <b>Oráculo de Masas V22 (Reddit / Twitter Sentiment):</b>\n"
            f"Veredicto NLP: <b>{sn['Veredicto']}</b> ({sn_emoji}: <code>{sn['Score']}/10</code>)\n"
            f"Lectura Cuántica: <i>{sn['Resumen_Cognitivo']}</i>\n"
        )
        
    if "Comite_Agentico" in report and report["Comite_Agentico"]:
        com = report["Comite_Agentico"]
        aud = com.get("Auditoria_Comite", {})
        status_consenso = "✅ CONSENSO UNÁNIME ALCANZADO ✅" if com.get("Consenso_Alcanzado") else "❌ VETADO (Riesgo Excesivo) ❌"
        
        mensaje += (
            f"\n🏛️ <b>COMITË MULTI-AGENTE V24 (Debate en Vivo):</b>\n"
            f"Veredicto Institucional: <b>{status_consenso}</b>\n"
            f"🎯 <b>Sniper ({aud.get('Sniper_Fuerza', 0)}/10)</b>: <i>...{aud.get('Sniper_Pto_Vista', '')[:100]}...</i>\n"
            f"🛡️ <b>Risk Manager</b>: <i>...{aud.get('Risk_Manager_Pto_Vista', '')[:100]}...</i>\n"
            f"🌍 <b>Macro Econ</b>: <i>...{aud.get('Macro_Econ_Pto_Vista', '')[:100]}...</i>\n"
        )
        
    if "RL_State" in report:
        estado_rl = report["RL_State"]
        try:
            from rl_agent_v2 import rl_engine
            q_vals = rl_engine.q_table.get(estado_rl, {"LONG": 0.0, "SHORT": 0.0, "WAIT": 0.0})
            mensaje += (
                f"\n⚙️ <b>Q-Learning V26 (Aprendizaje por Refuerzo):</b>\n"
                f"Dimensiones de Entorno: <code>{estado_rl}</code>\n"
                f"Dopamina LONG:  <b>{q_vals.get('LONG', 0.0):.4f}</b>\n"
                f"Dopamina SHORT: <b>{q_vals.get('SHORT', 0.0):.4f}</b>\n"
            )
        except Exception:
            pass
            
    mensaje += (
        f"\n📈 <b>Plan LONG (Compra Alcista)</b>\n"
        f"↳ Entrada: <code>{report['Long']['Entry']}</code>\n"
        f"↳ TP Sugerido: <code>{report['Long']['TP']}</code>\n"
        f"↳ SL Sugerido: <code>{report['Long']['SL']}</code>\n\n"

        f"📉 <b>Plan SHORT (Venta Bajista)</b>\n"
        f"↳ Entrada: <code>{report['Short']['Entry']}</code>\n"
        f"↳ TP Sugerido: <code>{report['Short']['TP']}</code>\n"
        f"↳ SL Sugerido: <code>{report['Short']['SL']}</code>\n\n"
        
        f"🧠 <b>Comentarios del Trader Fundador (IA):</b>\n"
        f"<i>{str(report.get('AI_Analysis', '')).replace('<', '&lt;').replace('>', '&gt;')}</i>\n"
    )
    
    await update.message.reply_html(mensaje, disable_web_page_preview=True)
    
    # Generar Gráfico Visual en tiempo real
    chart_generator.generar_grafico(simbolo, intervalo, "chart.png")
    
    if os.path.exists("chart.png"):
        try:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open("chart.png", "rb"))
        except Exception as e:
            logger.error(f"Error enviando gráfico: {e}")

async def send_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el dashboard del fondo de Binance Testnet"""
    chat_id = update.effective_chat.id
    if chat_id != ALLOWED_USER_ID:
        return
        
    import broker_real_binance as bb
    try:
        balance_actual = bb.obtener_balance_usdt()
        abiertas_binance = []
        
        posiciones = bb.client.account().get('positions', [])
        for p in posiciones:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                side = "LONG" if amt > 0 else "SHORT"
                pnl = float(p.get("unrealizedProfit", 0))
                abiertas_binance.append({
                    "symbol": p.get("symbol"),
                    "side": side,
                    "entry": float(p.get("entryPrice", 0)),
                    "pnl": pnl
                })
                    
        titulo = "🟢 OMNI-QUANT BINANCE FUND 🟢"
        
        msg = (
            f"🏦 <b>DASHBOARD: {titulo}</b> 🏦\n\n"
            f"💰 Balance Institucional: <code>${balance_actual:,.2f}</code>\n"
        )
        
        msg += f"\n📡 <b>OPERACIONES VIVAS EN EL MERCADO ({len(abiertas_binance)}):</b>\n"
        if len(abiertas_binance) == 0:
            msg += "<i>No hay trades activos en este momento.</i>\n"
        else:
            for t in abiertas_binance:
                emo_flot = "🟢" if t['pnl'] > 0 else "🔴"
                msg += f"• <b>{t['symbol']}</b> | {t['side']} @ ${t['entry']:.2f} | PnL: {emo_flot} <b>${t['pnl']:.2f}</b>\n"
                
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Falla balance: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error leyendo Exchange: {e}")

async def job_panic_kill_switch(context: ContextTypes.DEFAULT_TYPE):
    """Monitor de PnL Flotante para detectar desangrado brutal > -3.00% y apretar el Botón Rojo."""
    import broker_real_binance as bb
    import os
    if os.path.exists("HALT_TRADING.flag"):
        return # Ya estamos suspendidos
        
    try:
        balance = bb.obtener_balance_usdt()
        if balance <= 0: return
        
        posiciones_risk = bb.client.get_position_risk()
        pnl_total = 0.0
        abiertas = []
        
        for p in posiciones_risk:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                upnl = float(p.get("unRealizedProfit", 0))
                pnl_total += upnl
                abiertas.append(p.get("symbol"))
                
        drawdown_pct = pnl_total / balance
        
        if drawdown_pct <= -0.03:
            logger.critical(f"🚨 KILL PÁNICO DISPARADO! Drawdown de {drawdown_pct*100:.2f}% excedió límite (-3.00%).")
            
            # 1. Congelar ejecución futura globalmente con recuperación autónoma
            import json, time
            with open("HALT_TRADING.flag", "w") as f:
                state = {
                    "halt_timestamp": time.time(),
                    "cooldown_hours": 3,
                    "reason": f"Kill Switch activado. PNL Flotante: {pnl_total} USDT"
                }
                json.dump(state, f)
                
            # 2. Cancelar todo en el libro de órdenes y cerrar a mercado
            for sym in abiertas:
                try:
                    bb.client.cancel_open_orders(symbol=sym)
                    
                    pos_info = [x for x in posiciones_risk if x['symbol'] == sym][0]
                    ant = float(pos_info['positionAmt'])
                    side_to_close = "SELL" if ant > 0 else "BUY"
                    
                    bb.client.new_order(
                        symbol=sym,
                        side=side_to_close,
                        type="MARKET",
                        quantity=str(abs(ant))
                    )
                except Exception as e_close:
                    logger.error(f"Fallo al liquidar mercado {sym}: {e_close}")
            
            # Emitir advertencia roja
            msg = (
                "🚨📉 <b>EMERGENCIA: SISTEMA GRAVITACIONAL COMPROMETIDO</b> 📉🚨\n\n"
                f"El Portafolio alcanzó una pérdida flotante de <b>{drawdown_pct*100:.2f}%</b> (<code>${pnl_total:.2f} USDT</code>).\n"
                "🛡️ <b>ACCION EVASIVA EJECUTADA:</b>\n"
                "- Todas las posiciones fueron Liquidadas.\n"
                "- Mallas y Órdenes Pendientes Destruidas.\n"
                "- Sistema en Coma Inducido. Reactivación Autónoma en <b>3 Horas</b>.\n"
            )
            await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=msg, parse_mode='HTML')
            
    except Exception as e:
        logger.error(f"Falla rutinaria del Kill Switch: {e}")

async def job_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """Envía un reporte diario de PnL a las 23:59 (Migrado a Binance Testnet)."""
    import broker_real_binance as bb
    try:
        balance_actual = bb.obtener_balance_usdt()
        abiertas_binance = []
        
        posiciones = bb.client.account().get('positions', [])
        for p in posiciones:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                side = "LONG" if amt > 0 else "SHORT"
                pnl = float(p.get("unrealizedProfit", 0))
                abiertas_binance.append({
                    "symbol": p.get("symbol"),
                    "side": side,
                    "entry": float(p.get("entryPrice", 0)),
                    "pnl": pnl
                })

        titulo_cierre = "🟢 CIERRE DIARIO DEL MERCADO: BINANCE FUND 🟢"
        msg = (
            f"{titulo_cierre}\n\n"
            f"💰 Balance Actual: <code>${balance_actual:,.2f}</code>\n"
        )
        
        msg += f"\n📡 <b>OPERACIONES VIVAS (PnL Flotante) ({len(abiertas_binance)}):</b>\n"
        if len(abiertas_binance) == 0:
            msg += "<i>No hay trades activos abiertos para mañana. Mercado en blanco.</i>\n"
        else:
            for t in abiertas_binance:
                emo_flot = "🟢" if t['pnl'] > 0 else "🔴"
                msg += f"• <b>{t['symbol']}</b> | {t['side']} @ ${t['entry']:.2f} | PnL: {emo_flot} <b>${t['pnl']:.2f}</b>\n"
                
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error enviando Reporte Diario Binance: {e}")

async def job_hourly_report(context: ContextTypes.DEFAULT_TYPE):
    """Reporte táctico cada 1 hora con contexto HMM y Estado Técnico."""
    import broker_real_binance as bb
    import json
    try:
        # PnL Flotante y Realizado
        balance = bb.obtener_balance_usdt()
        saldo_base = 5000.0  # Fondeo inicial Testnet
        pnl_realizado = balance - saldo_base
        emo_real = "🟢" if pnl_realizado > 0 else "🔴" if pnl_realizado < 0 else "⚪"
        
        posiciones = bb.client.account().get('positions', [])
        pnl_flotante_total = sum([float(p.get("unrealizedProfit", 0)) for p in posiciones if float(p.get("positionAmt", 0)) != 0])
        trade_count = sum([1 for p in posiciones if float(p.get("positionAmt", 0)) != 0])
        emo_total = "🟢" if pnl_flotante_total > 0 else "🔴" if pnl_flotante_total < 0 else "⚪"

        msg = f"⏱️ <b>REPORTE TÁCTICO BI-HORARIO</b> ⏱️\n\n"
        msg += f"💰 Billetera Virtual: <code>${balance:,.2f}</code>\n"
        msg += f"📈 P&L Balance Realizado (Ganancia/Pérdida Neta): {emo_real} <b>${pnl_realizado:.2f}</b>\n"
        msg += f"📉 P&L Global Flotante: {emo_total} <b>${pnl_flotante_total:.2f}</b> ({trade_count} Posiciones HMM Abiertas)\n\n"
        
        # Leer cache para métricas
        try:
            with open("quant_market_cache.json", "r") as f:
                ch = json.load(f)
                msg += "🌐 <b>ESTADO DE MERCADO:</b>\n"
                for sym, data in ch.items():
                    # Solo enviar el reporte de las permitidas para no espamear
                    if sym in ["BTC-USD", "ETH-USD", "BNB-USD"]:
                        trend = data.get("Trend_4H", "N/A")
                        adx = data.get("ADX", "N/A")
                        msg += f"🔸 <b>{sym}</b>: ADX({adx}) | {trend.split('|')[-1].strip()}\n"
        except:
            msg += "<i>[No se encontraron datos técnicos recientes en caché]</i>\n"

        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error Reporte Horario: {e}")

async def trigger_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fuerza el envío del reporte táctico bajo demanda del usuario."""
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text("⏳ Procesando reporte táctico y consultando caché de liquidez...")
    await job_hourly_report(context)

async def job_evolucion_semanal(context: ContextTypes.DEFAULT_TYPE):
    """Desencadena la Minería de Fuerza Bruta Genética en el servidor."""
    import subprocess
    msg = (
        f"🧬 <b>SISTEMA DE AUTOCURACIÓN INICIADO</b> 🧬\n\n"
        f"He acoplado el Enjambre Optuna a los procesadores.\n"
        f"Durante las próximas horas, tu Agente intentará encontrar mejores Hiperparámetros (ADN) adaptados a la última semana de mercado para reemplazar silenciosamente el antiguo `settings_v2.json`.\n"
        f"¡Te notificaré al terminar!"
    )
    try:
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=msg, parse_mode='HTML')
        
        # Declarar tarea asíncrona para no bloquear el bot
        async def run_optuna_and_notify():
            try:
                # Correr de manera asíncrona
                import asyncio
                import json
                import sys
                proc = await asyncio.create_subprocess_exec(sys.executable, "optuna_swarm.py")
                await proc.wait()
                
                # Leer la nueva genética para reportarla
                res_msg = "🧬 <b>CURACIÓN COMPLETADA CON ÉXITO</b> 🧬\n\nNuevos Genomas:\n"
                try:
                    with open("settings_v2.json", "r") as f:
                        nuevos = json.load(f)
                    for sym, data in nuevos.items():
                        res_msg += f"🔸 <b>{sym}</b>: EMA({data['EMA_SHORT']},{data['EMA_LONG']}) | ADX > {data['ADX_THRESH']}\n"
                except Exception as ex:
                    res_msg += "\n⚠️ Parámetros analizados, pero no se encontró el archivo exportado (Posible falta de permisos o DB externa apuntando a otro lado)."
                
                await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=res_msg, parse_mode='HTML')
            except Exception as e:
                await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=f"⚠️ Falla crítica en subproceso de autocuración: {e}")

        # Lanzar al event loop
        import asyncio
        asyncio.create_task(run_optuna_and_notify())
        
    except Exception as e:
        logger.error(f"Error invocando Evolucion: {e}")

async def arbitraje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Escanea el Z-Score de Cointegración (Delta-Neutral) en vivo."""
    if update.effective_user.id != ALLOWED_USER_ID:
        return
        
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text="⚖️ <i>Calculando Desviación Típica (Z-Score) del Spread BTC/ETH...</i>",
        parse_mode='HTML'
    )
    
    try:
        res = arbitrage_engine_v2.calcular_zscore_historico("BTC-USD", "ETH-USD")
        if "Error" in res:
            await update.message.reply_text(f"❌ Error en Arbitraje: {res['Error']}")
            return
            
        emoji = res["Emoji"]
        mensaje = (
            f"{emoji} <b>ALERTA DE PAIRS TRADING (COINTEGRACIÓN)</b> {emoji}\n\n"
            f"<b>Activos:</b> {res['Ticker_A']} vs {res['Ticker_B']}\n"
            f"<b>Precios:</b> ${res['Price_A']} / ${res['Price_B']}\n"
            f"<b>Ratio Actual (Spread):</b> <code>{res['Current_Ratio']}</code>\n"
            f"<b>Media Histórica (SMA):</b> <code>{res['Mean_Ratio']}</code>\n\n"
            f"📊 <b>Z-SCORE MARGINAL:</b> <code>{res['Z_Score']}</code>\n\n"
            f"<b>Acción Matemática:</b>\n"
            f"<i>{res['Recomendacion']}</i>\n"
        )
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=mensaje,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Fallo en endpoint arbitraje: {e}")
        await update.message.reply_text("❌ Ha ocurrido un error calculando el Z-Score.")

async def handle_texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ALLOWED_USER_ID:
        return
        
    texto_usuario = update.message.text
    user_id = update.effective_user.id
    
    # Acción de escribiendo
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # Procesar lógica (Protegido en Hilo Paralelo para no bloquear Telegram)
    loop = asyncio.get_running_loop()
    respuesta = await loop.run_in_executor(None, chatear_con_ia, texto_usuario, user_id)
    
    # Paginación para evitar "Telegram BadRequest: Text is too long" (límite 4096 chars)
    if len(respuesta) > 4000:
        for i in range(0, len(respuesta), 4000):
            await update.message.reply_text(respuesta[i:i+4000])
    else:
        await update.message.reply_text(respuesta)

async def inciar_demonios(application: Application) -> None:
    """Hook asíncrono para ejecutar demonios en paralelo al bot."""
    logger.info("Iniciando Demonio HFT (Tape Reader) en silencio (Segundo Plano)...")
    tape = hft_tape_reader.TapeReader(telegram_bot=None, chat_id=None)
    asyncio.create_task(tape.connect_and_listen())
    
    logger.info("Despertando Escáner Cuantitativo OMNI y Rastreador OCO Bybit...")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, omni_scanner_daemon.escanear_mercado_live)
    loop.run_in_executor(None, trailing_tracker.rastrear_trades_oco_live)

def main() -> None:
    if TELEGRAM_TOKEN == "REEMPLAZA_CON_TU_TOKEN":
        logger.warning("No has establecido tu TELEGRAM_TOKEN.")

    if not os.environ.get("GEMINI_API_KEY"):
        logger.warning("ALERTA: Falta GEMINI_API_KEY en variables de entorno para usar la IA.")

    # Acabamos de inyectar el Radar HFT, OCO y Scanner al ciclo de vida del bot
    from telegram.ext import Application
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(inciar_demonios).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("get_signal", get_signal))
    application.add_handler(CommandHandler("portfolio", send_portfolio))
    application.add_handler(CommandHandler("demo", send_portfolio))
    application.add_handler(CommandHandler("reporte", trigger_report))
    application.add_handler(CommandHandler("arbitraje", arbitraje))
    
    # Manejador de texto conversacional libre
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_texto_libre))

    import datetime
    import pytz
    
    caracas_tz = pytz.timezone('America/Caracas')
    
    # Reporte Táctico Bi-Horario (Cron Job Sincronizado cada hora par: 14:00, 16:00, etc)
    now_cs = datetime.datetime.now(caracas_tz)
    current_hour = now_cs.hour
    next_even_hour_val = current_hour + 2 if current_hour % 2 == 0 else current_hour + 1
    next_hour_dt = (now_cs.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=next_even_hour_val))
    delay_sec = (next_hour_dt - now_cs).total_seconds()
    
    # 1. Ejecución instantánea ELIMINADA para evitar spam en Hot Reloads

    # Kill Switch (Se evalúa cada 5 minutos buscando PnL negativo crítico)
    application.job_queue.run_repeating(
        job_panic_kill_switch,
        interval=300,
        first=60,
        name='panic_kill_switch'
    )

    # 2. Ejecución cíclica exacta cada 2 horas en punto
    application.job_queue.run_repeating(
        job_hourly_report,
        interval=7200,
        first=delay_sec,
        name='bihourly_tactical_report_sync'
    )
    
    # Reporte de Cierre de Mercado Diario a las 23:59 Hora de Caracas
    application.job_queue.run_daily(
        job_daily_report, 
        time=datetime.time(hour=23, minute=59, tzinfo=caracas_tz), 
        name='daily_pnl_report'
    )
    
    # EVOLUCION SEMANAL: Autocuración y Retraining. Lunes (0) a las 9:00 AM Caracas
    application.job_queue.run_daily(
        job_evolucion_semanal,
        time=datetime.time(hour=9, minute=0, tzinfo=caracas_tz),
        days=(0,), # Lunes = 0 en tuple
        name='weekly_genetic_retraining'
    )

    logger.info("Iniciando el bot Quant Pro v3 con IA...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Failed to start bot. Error: {e}")

if __name__ == '__main__':
    main()

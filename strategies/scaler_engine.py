import logging
from onchain_oracle import get_market_conviction
from social_oracle_v2 import evaluar_panico_masas

logger = logging.getLogger(__name__)

def calcular_conviccion(symbol: str, direccion_tecnica: str, adx_fuerza: float, fvg_bonus: bool = False, is_counter_trend: bool = False):
    """
    Calcula el Score de Convicción [1 al 10] uniendo Análisis Técnico, On-Chain y NLP (Sentimiento).
    """
    score = 0.0
    
    # --- PILAR ALPHA (INSTITUCIONAL FVG) ---
    if fvg_bonus and "BTC" in symbol:
        logger.info(f"[{symbol}] 🔥 ESTRUCTURA ALPHA DETECTADA: Intersección con FVG (Fair Value Gap). Aplastando Score (+3).")
        score += 3
        
    # --- DUAL-FRACTAL PENALTY ---
    if is_counter_trend:
        logger.info(f"[{symbol}] 🎭 MODO HIT & RUN (Scalp Contracorriente): Reduciendo fuerza de embate (-2 Puntos).")
        score -= 2
        
    # 1. PILAR TÉCNICO (Hasta 4 puntos)
    # ADX mide la fuerza de la tendencia. > 25 es tendencia fuerte, > 40 es huracán.
    if adx_fuerza > 40:
        score += 4
    elif adx_fuerza > 25:
        score += 2
    else:
        score += 1 # Base por tener señal ML alineada


    # 2. PILAR ON-CHAIN / LIQUIDEZ (Hasta 3 puntos)
    try:
        onchain_data = get_market_conviction(symbol)
        ls_score = onchain_data.get("conviction_score", 0) # Rango -5 a +5
        
        # Ajustamos a la dirección dictada por el técnico
        if direccion_tecnica == "BUY" and ls_score > 0:
            score += min(3, ls_score)
        elif direccion_tecnica == "SELL" and ls_score < 0:
            score += min(3, abs(ls_score))
        elif (direccion_tecnica == "BUY" and ls_score < -2) or (direccion_tecnica == "SELL" and ls_score > 2):
            score -= 2 # Choque fuerte entre Técnico y OnChain
    except Exception as e:
        logger.error(f"Fallo pilar On-Chain Conviction: {e}")
        
    # 3. PILAR SENTIMIENTO NLP (Hasta 3 puntos)
    try:
        resultado_social = evaluar_panico_masas(symbol) 
        sentimiento_social = resultado_social.get("Score", 0) / 10.0 # Normalizado a FLOAT entre -1 y 1
        
        if direccion_tecnica == "BUY":
            if sentimiento_social > 0.5:
                score += 3
            elif sentimiento_social > 0:
                score += 1
            elif sentimiento_social < -0.5:
                score -= 1 # Miedo excesivo, reduce score predictivo
        elif direccion_tecnica == "SELL":
            if sentimiento_social < -0.5:
                score += 3
            elif sentimiento_social < 0:
                score += 1
            elif sentimiento_social > 0.5:
                score -= 1 # Euforia extrema luchando contra la caída
    except Exception as e:
        logger.error(f"Fallo pilar Social Conviction: {e}")
        
    # Limitar Score a 1-10
    final_score = max(1, min(10, round(score)))
    
    # Mapear a Riesgo (Dynamic Sizing)
    # Nivel Institucional V5: Tamaño de Posición de Kelly
    kelly_base = calcular_kelly_historico()
    
    # Mapear Score a Fracción de Kelly (Dynamic Kelly Sizing)
    # Score 1-4 = Señal débil -> 1/4 Kelly
    # Score 5-6 = Señal regular -> 1/2 Kelly
    # Score 7-8 = Alta Probabilidad -> 3/4 Kelly
    # Score 9-10 = El Santo Grial "Sniper" -> Full Kelly (Máximo Riesgo Permisible)
    riesgo_pct = 0.0
    if final_score >= 9:
        riesgo_pct = kelly_base
    elif final_score >= 7:
        riesgo_pct = kelly_base * 0.75
    elif final_score >= 5:
        riesgo_pct = kelly_base * 0.50
    else:
        riesgo_pct = max(0.005, kelly_base * 0.25)
        
    logger.info(f"[{symbol}] Convicción: {final_score}/10 -> Asignación de Capital Ajustada (Mecánica Kelly): {riesgo_pct*100:.2f}%")
    return final_score, riesgo_pct

def calcular_kelly_historico():
    """Conecta a ByBit para extraer el Hit Rate y Factor de Ganancia y devolver la K Óptima."""
    import os
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from pybit.unified_trading import HTTP
        client = HTTP(testnet=False, api_key=os.environ.get("BYBIT_API_KEY"), api_secret=os.environ.get("BYBIT_SECRET"))
        res = client.get_closed_pnl(category="linear", limit=50)
        if res.get("retCode") == 0 and res["result"]["list"]:
            trades = res["result"]["list"]
            ganancias = []
            perdidas = []
            for t in trades:
                pnl = float(t["closedPnl"])
                if pnl > 0: ganancias.append(pnl)
                elif pnl < 0: perdidas.append(abs(pnl))
                
            total_trades = len(ganancias) + len(perdidas)
            if total_trades < 10:
                return 0.02 # Safe default si no hay historial
                
            W = len(ganancias) / total_trades
            avg_win = sum(ganancias) / len(ganancias) if len(ganancias) > 0 else 0
            avg_loss = sum(perdidas) / len(perdidas) if len(perdidas) > 0 else 0
            
            # Risk/Reward Ratio (R)
            if avg_loss > 0:
                R = avg_win / avg_loss
            else:
                R = 1.0 # Infinito
                
            # Kelly = W - ((1 - W) / R)
            if R > 0:
                kelly_pct = W - ((1.0 - W) / R)
            else:
                kelly_pct = 0.0
                
            # Kelly Fraccional (Mitad) es más seguro, con Cap fijo = 5%
            kelly_fraccional = kelly_pct * 0.5
            apuesta = max(0.005, min(0.05, kelly_fraccional))
            
            logger.info(f"📊 [Kelly Tracker DB] W: {W*100:.1f}%, R: {R:.2f} -> K-Optimo: {kelly_pct*100:.2f}% | Asignado Fijo: {apuesta*100:.2f}%")
            return apuesta
    except Exception as e:
        logger.error(f"Falla crítica estimando Kelly en vivo: {e}")
        
    return 0.02 # Safe fallback

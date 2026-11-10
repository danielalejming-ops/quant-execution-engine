import os
import pandas as pd

def detect_sweep(df: pd.DataFrame, window: int = 20) -> str:
    """
    Detecta Patrones de Barrido (Swing Failure Pattern) / Stop Hunts institucionales.
    Retorna "BULLISH_SWEEP" (buscar compras), "BEARISH_SWEEP" (buscar ventas) o "NONE".
    Revisa la última vela cerrada en busca de mechas largas que rompieron un nivel pero fueron rechazadas.
    """
    try:
        if len(df) < window + 2: return "NONE"
        
        # Analizamos la vela PREVIA (la última completamente cerrada)
        vela_previa = df.iloc[-2]
        cuerpo = abs(vela_previa['Close'] - vela_previa['Open'])
        
        # Corrección por Dojis perfectos para evitar división entre cero
        if cuerpo == 0: cuerpo = 0.00000001
            
        mecha_sup = vela_previa['High'] - max(vela_previa['Open'], vela_previa['Close'])
        mecha_inf = min(vela_previa['Open'], vela_previa['Close']) - vela_previa['Low']
        
        # Buscar el alto y bajo de la ventana térmica pasada
        historico = df.iloc[-(window+2):-2]
        max_reciente = historico['High'].max()
        min_reciente = historico['Low'].min()
        
        # 1. Bearish Sweep (Trampa Alcista)
        # Rompió el máximo hacia arriba, cazó stops, pero el bloque de venta institucional 
        # regresó el precio y cerró la vela por debajo del nivel roto.
        if vela_previa['High'] > max_reciente and vela_previa['Close'] < max_reciente:
            if mecha_sup > (cuerpo * 1.5): # Mecha superior domina (Fakeout)
                return "BEARISH_SWEEP"
                
        # 2. Bullish Sweep (Trampa Bajista)
        # Rompió el mínimo hacia abajo, causó pánico, pero compras institucionales 
        # absorbieron la oferta devolviendo el cierre por encima.
        if vela_previa['Low'] < min_reciente and vela_previa['Close'] > min_reciente:
            if mecha_inf > (cuerpo * 1.5): # Mecha inferior domina (Fakeout)
                return "BULLISH_SWEEP"
                
        return "NONE"
    except Exception:
        return "NONE"

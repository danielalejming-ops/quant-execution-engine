import os
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class SMCVisionEngine:
    """
    Motor Fractal Tridimensional (15m, 5m, 1m) para Osiris HFT.
    Detecta las 6 Zonas de Smart Money Concepts en tiempo real.
    """
    def __init__(self):
        self.lookback_fvg = 10
        self.lookback_ob = 20
        self.lookback_choch = 15
        
    def _detect_fvg(self, df: pd.DataFrame) -> dict:
        """
        Zonas 3 y 6: Detección de FVG (Fair Value Gap).
        Retorna el último FVG válido no mitigado.
        """
        if len(df) < 5: return {}
        
        # Analizamos las últimas velas (sin incluir la actual en formación)
        df_fvg = df.iloc[-(self.lookback_fvg+2):-1].copy()
        fvg_bullish = None
        fvg_bearish = None
        
        for i in range(2, len(df_fvg)):
            vela_1 = df_fvg.iloc[i-2]
            # vela_2 = df_fvg.iloc[i-1] # Vela fuerte
            vela_3 = df_fvg.iloc[i]
            
            # FVG Bullish (Alcista) -> Gap entre el High de vela 1 y Low de vela 3
            if vela_3['low'] > vela_1['high']:
                gap_size = vela_3['low'] - vela_1['high']
                # Si el gap es al menos 0.05% del precio (filtrar micro-ruido)
                if gap_size / vela_3['close'] > 0.0005:
                    fvg_bullish = {
                        'bottom': vela_1['high'],
                        'top': vela_3['low'],
                        'mid': (vela_1['high'] + vela_3['low']) / 2
                    }
                    
            # FVG Bearish (Bajista) -> Gap entre Low de vela 1 y High de vela 3
            if vela_3['high'] < vela_1['low']:
                gap_size = vela_1['low'] - vela_3['high']
                if gap_size / vela_3['close'] > 0.0005:
                    fvg_bearish = {
                        'top': vela_1['low'],
                        'bottom': vela_3['high'],
                        'mid': (vela_1['low'] + vela_3['high']) / 2
                    }
                    
        return {"Bullish": fvg_bullish, "Bearish": fvg_bearish}

    def _detect_ob_and_choch(self, df: pd.DataFrame) -> dict:
        """
        Zonas 1, 2, 4 y 5: Detecta Order Blocks, Change of Character (Falla estructural)
        y Barridos de Liquidez (Sweeps).
        """
        if len(df) < self.lookback_ob: return {}
        data = df.iloc[-self.lookback_ob:].copy()
        
        high_recent = data['high'].max()
        low_recent = data['low'].min()
        
        # Identificar la vela que causó el extremo
        idx_high = data['high'].idxmax()
        idx_low = data['low'].idxmin()
        
        ob_bullish = None
        ob_bearish = None
        choch_bullish = False
        choch_bearish = False
        liquidity_sweep_bull = False
        liquidity_sweep_bear = False
        
        # 1. BARRIDO (Sweeps) - Zona 1, 2, 5
        # Comparamos si la vela en curso barrió un mínimo anterior pero rechazó y cerró alto
        last_candle = data.iloc[-1]
        prev_candle = data.iloc[-2]
        if last_candle['low'] < low_recent * 1.0001 and last_candle['close'] > (last_candle['high'] + last_candle['low'])/2:
            liquidity_sweep_bull = True # Barrido de mínimos y absorción (Zona 5)
            
        if last_candle['high'] > high_recent * 0.9999 and last_candle['close'] < (last_candle['high'] + last_candle['low'])/2:
            liquidity_sweep_bear = True # Barrido de máximos y absorción
            
        # 2. CHOCH (Cambio de Carácter) - Falla de Alto Más Alto (Zona 4)
        if last_candle['high'] < prev_candle['high'] and last_candle['close'] < prev_candle['low']:
            # Zona 4: Falla de hacer un HH, seguido de un break a la baja
            choch_bearish = True
        
        if last_candle['low'] > prev_candle['low'] and last_candle['close'] > prev_candle['high']:
            # Posible ChoCh alcista tras estructura de rebote
            choch_bullish = True
            
        # 3. ORDER BLOCKS (Zona 3)
        # OB Bullish: Última vela bajista antes de impulso alcista fuerte
        if data.iloc[-3]['close'] < data.iloc[-3]['open'] and data.iloc[-2]['close'] > data.iloc[-2]['open']:
            if data.iloc[-2]['close'] > data.iloc[-3]['high']: # Envuelve
                ob_bullish = {
                    'top': data.iloc[-3]['high'],
                    'bottom': data.iloc[-3]['low']
                }
                
        # OB Bearish: Última vela alcista antes del drop de la Zona 4
        if data.iloc[-3]['close'] > data.iloc[-3]['open'] and data.iloc[-2]['close'] < data.iloc[-2]['open']:
            if data.iloc[-2]['close'] < data.iloc[-3]['low']:
                ob_bearish = {
                    'top': data.iloc[-3]['high'],
                    'bottom': data.iloc[-3]['low']
                }
                
        return {
            "OB_Bull": ob_bullish,
            "OB_Bear": ob_bearish,
            "ChoCh_Bull": choch_bullish,
            "ChoCh_Bear": choch_bearish,
            "Sweep_Bull": liquidity_sweep_bull,
            "Sweep_Bear": liquidity_sweep_bear
        }

    def _detect_liquidity_pools(self, df_15m: pd.DataFrame) -> dict:
        """
        Calcula:
        - PDH / PDL (Previous Day High / Low) basándose en ~24h (96 velas de 15m).
        - EQH / EQL (Equal Highs / Equal Lows) buscando picos locales.
        """
        pdh = None
        pdl = None
        if len(df_15m) >= 96:
            prev_day = df_15m.iloc[-192:-96] if len(df_15m) >= 192 else df_15m.iloc[:-96]
            if not prev_day.empty:
                pdh = prev_day['high'].max()
                pdl = prev_day['low'].min()
                
        eqh = None
        eql = None
        if len(df_15m) >= 50:
            recent = df_15m.iloc[-50:-1].reset_index(drop=True)
            peaks = []
            bottoms = []
            for i in range(2, len(recent)-2):
                h0, h1, h2, h3, h4 = recent.iloc[i-2:i+3]['high']
                if h2 > h0 and h2 > h1 and h2 > h3 and h2 > h4:
                    peaks.append(h2)
                l0, l1, l2, l3, l4 = recent.iloc[i-2:i+3]['low']
                if l2 < l0 and l2 < l1 and l2 < l3 and l2 < l4:
                    bottoms.append(l2)
            
            peaks.sort(reverse=True)
            for i in range(len(peaks)-1):
                if abs(peaks[i] - peaks[i+1]) / peaks[i] < 0.0005:
                    eqh = peaks[i]
                    break
                    
            bottoms.sort()
            for i in range(len(bottoms)-1):
                if abs(bottoms[i] - bottoms[i+1]) / bottoms[i] < 0.0005:
                    eql = bottoms[i]
                    break
                    
        return {"PDH": pdh, "PDL": pdl, "EQH": eqh, "EQL": eql}

    def analyze_fractal_cubes(self, current_price: float, df_15m: pd.DataFrame, df_5m: pd.DataFrame, df_1m: pd.DataFrame) -> dict:
        """
        Cerebro Principal. Pondera el contexto 15m/5m y escanea gatillos de 1m.
        """
        # --- 1. Bias Bias Macroeconómico (El Mapa - 15m) ---
        # Simplemente leemos la EMA o la tendencia pura de 15m
        if len(df_15m) < 10: return {"signal": "WAIT"}
        ema_15m = df_15m['close'].ewm(span=9, adjust=False).mean().iloc[-1]
        macro_bias = "BULLISH" if current_price > ema_15m else "BEARISH"
        
        # --- 2. Filtro Telescopio (El Estabilizador - 5m) ---
        # Buscamos Order Blocks en 5m para validación fuerte
        smc_5m = self._detect_ob_and_choch(df_5m)
        ob_5m_bull = smc_5m.get("OB_Bull")
        ob_5m_bear = smc_5m.get("OB_Bear")
        
        # --- 3. Microscopio de Gatillo (El Ametrallador V8 - 1m) ---
        smc_1m = self._detect_ob_and_choch(df_1m)
        fvg_1m = self._detect_fvg(df_1m)
        fvg_5m = self._detect_fvg(df_5m) # Detección FVG Alto Calibre (5m+)
        liq_pools = self._detect_liquidity_pools(df_15m)
        
        signal = "WAIT"
        reason = ""

        # --- 4. PREDICTIVE AI: Swing Failure Pattern (SFP) Liquidity Sweep ---
        last_1m = df_1m.iloc[-1]
        sfp_bullish = False
        sfp_bullish_source = ""
        sfp_bearish = False
        sfp_bearish_source = ""
        pdh, pdl, eqh, eql = liq_pools["PDH"], liq_pools["PDL"], liq_pools["EQH"], liq_pools["EQL"]
        
        for lvl, src in [(eqh, "EQH"), (pdh, "PDH")]:
            if lvl and last_1m['high'] > lvl > last_1m['close']:
                # Rechazo bajista tras romper máximo
                if last_1m['close'] < (last_1m['high'] + last_1m['low']) / 2:
                    sfp_bearish = True
                    sfp_bearish_source = f"SFP en {src}"
                    
        for lvl, src in [(eql, "EQL"), (pdl, "PDL")]:
            if lvl and last_1m['low'] < lvl < last_1m['close']:
                # Absorción alcista tras romper mínimo
                if last_1m['close'] > (last_1m['high'] + last_1m['low']) / 2:
                    sfp_bullish = True
                    sfp_bullish_source = f"SFP en {src}"
        
        # EVALUACIÓN DE COMPRA (LONG)
        if sfp_bullish:
            signal = "BUY"
            reason = f"PREDICTIVO: {sfp_bullish_source} (Liquidación de Retailers y Rechazo)"
        elif macro_bias == "BULLISH":
            # Si hay FVG de 5 Minutos (Vanguardia), le damos prioridad como Gatillo Alpha
            if fvg_5m.get("Bullish") and current_price <= fvg_5m["Bullish"]["top"] * 1.001 and current_price >= fvg_5m["Bullish"]["bottom"] * 0.999:
                signal = "BUY"
                reason = "ZONA ALPHA SMC (Retesteo FVG 5m - High Probability)"
            # Gatillo Zona 6: Entrada en FVG 1m
            elif fvg_1m.get("Bullish") and current_price <= fvg_1m["Bullish"]["top"] and current_price >= fvg_1m["Bullish"]["bottom"]:
                signal = "BUY"
                reason = "ZONA 6 SMC (Retesteo FVG 1m)"
                
            # Gatillo Zona 3: Mitigación de OB
            elif smc_1m.get("OB_Bull") and current_price <= smc_1m["OB_Bull"]["top"] and current_price >= smc_1m["OB_Bull"]["bottom"]:
                signal = "BUY"
                reason = "ZONA 3 SMC (Mitigación OB 1m)"
                
            # Gatillo Zona 1, 2, 5: Barrido Liquidez e Inducción
            elif smc_1m.get("Sweep_Bull") and smc_1m.get("ChoCh_Bull"):
                signal = "BUY"
                reason = "ZONAS 1/2/5 SMC (Barrido Liquidez + ChoCh Alcista)"

            # Validar con OB 5m si está muy cerca
            if signal == "BUY" and ob_5m_bear and current_price >= ob_5m_bear["bottom"] * 0.999:
                # Cancelado por resistencia gruesa en 5m
                signal = "WAIT"
                reason = "VETO MTF: Muro OB Bajista en 5m"

        # EVALUACIÓN DE VENTA (SHORT)
        elif sfp_bearish:
            signal = "SELL"
            reason = f"PREDICTIVO: {sfp_bearish_source} (Liquidación de Retailers y Rechazo)"
        elif macro_bias == "BEARISH":
            # Gatillo Alpha FVG 5m
            if fvg_5m.get("Bearish") and current_price >= fvg_5m["Bearish"]["bottom"] * 0.999 and current_price <= fvg_5m["Bearish"]["top"] * 1.001:
                signal = "SELL"
                reason = "ZONA ALPHA SMC (Retesteo FVG Bajista 5m - High Probability)"
            # Gatillo Zona 4: Falla de altos y caída
            elif smc_1m.get("ChoCh_Bear") or smc_1m.get("Sweep_Bear"):
                signal = "SELL"
                reason = "ZONA 4 SMC (Distribución / Falla Alto Más Alto)"
                
            elif fvg_1m.get("Bearish") and current_price >= fvg_1m["Bearish"]["bottom"] and current_price <= fvg_1m["Bearish"]["top"]:
                signal = "SELL"
                reason = "SMC (Retesteo FVG Bajista 1m)"
                
            elif smc_1m.get("OB_Bear") and current_price >= smc_1m["OB_Bear"]["bottom"] and current_price <= smc_1m["OB_Bear"]["top"]:
                signal = "SELL"
                reason = "SMC (Mitigación OB Bajista 1m)"
                
            if signal == "SELL" and ob_5m_bull and current_price <= ob_5m_bull["top"] * 1.001:
                signal = "WAIT"
                reason = "VETO MTF: Soporte OB Alcista en 5m"
                
        return {
            "signal": signal,
            "reason": reason,
            "macro_bias": macro_bias,
            "liq_pools": liq_pools,
            "fvg_1m": fvg_1m,
            "fvg_5m": fvg_5m
        }

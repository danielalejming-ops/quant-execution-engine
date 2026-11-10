
import pandas_ta as ta
import pandas as pd
import logging
import os
import requests
import time
import concurrent.futures
from dotenv import load_dotenv
from ml_engine_v2 import predecir_direccion_ml
import news_oracle
import asyncio
from social_oracle import SocialOracle
from rl_agent_v2 import rl_engine
import hmm_engine
import rag_engine  # Memoria Perenne

# --- CONFIGURACIÓN DE PARÁMETROS MATEMÁTICOS DE LA ESTRATEGIA (ACTUALIZADOS POR BACKTESTER) ---

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import anthropic
import json

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
try:
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
except:
    anthropic_client = None
# Parámetros de Configuración del Motor de Trading
EMA_SHORT_PERIOD = 9
EMA_LONG_PERIOD = 21
ADX_THRESHOLD = 25
RSI_BUY_MAX = 70
RSI_SELL_MIN = 30

# Memoria de chat global en memoria RAM
USER_MEMORIES = {}

def map_symbol(sym: str) -> str:
    """Mapea símbolos amigables a los estándares de Yahoo Finance."""
    sym = sym.upper()
    # Mapeo Cripto USDT a USD para YFinance
    if sym.endswith("USDT") and len(sym) >= 7:
        return sym.replace("USDT", "-USD")
    if sym == "BTC": return "BTC-USD"
    if sym == "ETH": return "ETH-USD"
    # Mapeo básico Forex si se omite =X
    if len(sym) == 6 and not "-" in sym and not "=" in sym:
        # Ejemplo: EURUSD -> EURUSD=X
        return f"{sym}=X"
    return sym

def obtener_actividad_ballenas(simbolo: str, umbral_usd: float = 100000.0) -> dict:
    """Extrae las operaciones públicas en vivo de Binance Futures (AggTrades) para detectar manipulación de Ballenas y liquidaciones."""
    try:
        import requests
        sym = simbolo.upper().replace("-USD", "USDT")
        if not sym.endswith("USDT"): sym += "USDT"
        
        # Obtenemos los últimos 1000 trades del mercado
        url = "https://fapi.binance.com/fapi/v1/aggTrades"
        params = {"symbol": sym, "limit": 1000}
        resp = requests.get(url, params=params, timeout=3)
        if resp.status_code == 200:
            trades = resp.json()
            vol_long_ballenas = 0.0
            vol_short_ballenas = 0.0
            conteo_anormales = 0
            
            for t in trades:
                precio = float(t.get('p', 0))
                qty = float(t.get('q', 0))
                valor_usd = precio * qty
                
                if valor_usd >= umbral_usd:
                    conteo_anormales += 1
                    # 'm' = isTheBuyerMaker. Si m=True, el agresor fue Vendedor (SHORT a mercado)
                    # Si m=False, el agresor fue Comprador (LONG a mercado)
                    if t.get('m', False):
                        vol_short_ballenas += valor_usd
                    else:
                        vol_long_ballenas += valor_usd
                        
            total_vol = vol_long_ballenas + vol_short_ballenas
            if total_vol > 0:
                dominancia = "ALCISTA (Acumulación Institucional Fuerte)" if vol_long_ballenas > vol_short_ballenas else "BAJISTA (Descarga Institucional Fuerte)"
            else:
                dominancia = "NEUTRAL (Sin rastro de ballenas)"
                
            return {
                "Estado": "Activo",
                "Total_Ballenas_Detectadas": conteo_anormales,
                "Volumen_Longs_Superiores": vol_long_ballenas,
                "Volumen_Shorts_Superiores": vol_short_ballenas,
                "Dominancia": dominancia
            }
    except Exception as e:
        logger.warning(f"Error extrayendo On-Chain Whales: {e}")
    return {"Estado": "Inactivo / Error", "Total_Ballenas_Detectadas": 0}

def obtener_order_book(simbolo: str) -> dict:
    """Extrae el Macro Order Book de Binance, Bybit, Coinbase, OKX y Kraken en paralelo."""
    sym = simbolo.upper()
    if not (sym.endswith("-USD") or sym.endswith("USDT")):
        return None
        
    base_asset = sym.replace("-USD", "").replace("USDT", "")
    
    binance_sym = f"{base_asset}USDT"
    coinbase_sym = f"{base_asset}-USD"
    okx_sym = f"{base_asset}-USDT"
    kraken_sym = f"{base_asset}USDT"
    
    def fetch_binance():
        resp = requests.get("https://api.binance.com/api/v3/depth", params={"symbol": binance_sym, "limit": 20}, timeout=2)
        d = resp.json()
        return [{"price": float(b[0]), "qty": float(b[1])} for b in d.get("bids", [])], [{"price": float(a[0]), "qty": float(a[1])} for a in d.get("asks", [])]
        
    def fetch_bybit():
        resp = requests.get("https://api.bybit.com/v5/market/orderbook", params={"category": "spot", "symbol": binance_sym, "limit": 20}, timeout=2)
        d = resp.json()
        if "result" in d and d["result"]:
            return [{"price": float(b[0]), "qty": float(b[1])} for b in d["result"].get("b", [])], [{"price": float(a[0]), "qty": float(a[1])} for a in d["result"].get("a", [])]
        return [], []
        
    def fetch_coinbase():
        resp = requests.get(f"https://api.exchange.coinbase.com/products/{coinbase_sym}/book?level=2", headers={"User-Agent": "BotTrader/1.0"}, timeout=2)
        d = resp.json()
        return [{"price": float(b[0]), "qty": float(b[1])} for b in d.get("bids", [])[:20]], [{"price": float(a[0]), "qty": float(a[1])} for a in d.get("asks", [])[:20]]
        
    def fetch_okx():
        resp = requests.get("https://www.okx.com/api/v5/market/books", params={"instId": okx_sym, "sz": 20}, timeout=2)
        d = resp.json()
        if "data" in d and len(d["data"]) > 0:
            return [{"price": float(b[0]), "qty": float(b[1])} for b in d["data"][0].get("bids", [])], [{"price": float(a[0]), "qty": float(a[1])} for a in d["data"][0].get("asks", [])]
        return [], []
        
    def fetch_kraken():
        resp = requests.get("https://api.kraken.com/0/public/Depth", params={"pair": kraken_sym, "count": 20}, timeout=2)
        d = resp.json()
        if d.get("error"):
            # Fallback for Kraken to BTCUSD or ETHUSD etc.
            kraken_fallback = f"{base_asset}USD"
            resp = requests.get("https://api.kraken.com/0/public/Depth", params={"pair": kraken_fallback, "count": 20}, timeout=2)
            d = resp.json()
            if d.get("error"):
                return [], []
        pair_key = list(d["result"].keys())[0]
        return [{"price": float(b[0]), "qty": float(b[1])} for b in d["result"][pair_key].get("bids", [])], [{"price": float(a[0]), "qty": float(a[1])} for a in d["result"][pair_key].get("asks", [])]

    all_bids = []
    all_asks = []
    exchanges_ok = 0
    
    fetchers = [
        (fetch_binance, "Binance"),
        (fetch_bybit, "Bybit"),
        (fetch_okx, "OKX")
    ]
    
    for fetch_func, name in fetchers:
        try:
            b, a = fetch_func()
            if b or a:
                exchanges_ok += 1
                all_bids.extend(b)
                all_asks.extend(a)
        except Exception as e:
            logger.warning(f"Error fetching OB from {name}: {e}")
                
    if not all_bids and not all_asks:
        return None
        
    # Sum of all available volumes across exchanges in the top 20 visible depth
    vol_compras = sum(b["qty"] for b in all_bids)
    vol_ventas = sum(a["qty"] for a in all_asks)
    
    # Highest single volume wall found across any specific price level
    max_bid = max(all_bids, key=lambda x: x["qty"]) if all_bids else {"price": 0, "qty": 0}
    max_ask = max(all_asks, key=lambda x: x["qty"]) if all_asks else {"price": 0, "qty": 0}
    
    return {
        "Presion_Compra": round(vol_compras, 2),
        "Presion_Venta": round(vol_ventas, 2),
        "Muro_Compra": {"Precio": float(max_bid["price"]), "Volumen": round(float(max_bid["qty"]), 2)},
        "Muro_Venta": {"Precio": float(max_ask["price"]), "Volumen": round(float(max_ask["qty"]), 2)},
        "Exchanges_Agregados": exchanges_ok
    }

def obtener_cvd_binance(simbolo: str, intervalo: str, limit: int = 50) -> float:
    """Calcula el Cumulative Volume Delta (CVD) real usando los Taker Buy/Sell Volumes de Binance API."""
    sym = simbolo.upper()
    if not (sym.endswith("-USD") or sym.endswith("USDT") or sym == "BTC" or sym == "ETH"):
        return None
        
    base_asset = sym.replace("-USD", "").replace("USDT", "")
    binance_sym = f"{base_asset}USDT"
    
    intervalos_validos = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]
    if intervalo not in intervalos_validos:
        intervalo = "1d"
        
    try:
        url = "https://api.binance.com/api/v3/klines"
        resp = requests.get(url, params={"symbol": binance_sym, "interval": intervalo, "limit": limit}, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            cvd = 0.0
            for k in data:
                total_vol = float(k[5])
                taker_buy_vol = float(k[9])
                taker_sell_vol = total_vol - taker_buy_vol
                delta = taker_buy_vol - taker_sell_vol
                cvd += delta
            return round(cvd, 4)
    except Exception as e:
        logger.warning(f"Error fetching CVD de {binance_sym}: {e}")
    return None

def obtener_funding_rate(simbolo: str) -> str:
    """Obtiene el Funding Rate actual para el mercado de futuros perpetuos en Binance."""
    sym = simbolo.upper()
    if not (sym.endswith("-USD") or sym.endswith("USDT") or sym == "BTC" or sym == "ETH"):
        return None
    
    base_asset = sym.replace("-USD", "").replace("USDT", "")
    binance_sym = f"{base_asset}USDT"
    
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        resp = requests.get(url, params={"symbol": binance_sym}, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            fr = float(data.get("lastFundingRate", 0))
            fr_pct = fr * 100
            if fr_pct > 0:
                return f"🟢 {fr_pct:.4f}% (Longs pagan a Shorts)"
            elif fr_pct < 0:
                return f"🔴 {fr_pct:.4f}% (Shorts pagan a Longs)"
            else:
                return f"⚪ {fr_pct:.4f}% (Neutral)"
    except Exception as e:
        logger.warning(f"Error fetching Funding Rate de {binance_sym}: {e}")
    return None

def obtener_fear_and_greed() -> str:
    """Obtiene el Crypto Fear & Greed Index global."""
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            if data and "data" in data and len(data["data"]) > 0:
                val = data["data"][0]["value"]
                clas = data["data"][0]["value_classification"]
                return f"{val}/100 ({clas})"
    except Exception as e:
        logger.warning(f"Error fetching Fear and Greed Index: {e}")
    return None

def obtener_footprint(simbolo: str) -> dict:
    """Extrae el perfil de volumen (Footprint / Order Flow) de los últimos 1000 trades en Binance."""
    sym = simbolo.upper()
    if not (sym.endswith("-USD") or sym.endswith("USDT") or sym == "BTC" or sym == "ETH"):
        return None
        
    base_asset = sym.replace("-USD", "").replace("USDT", "")
    binance_sym = f"{base_asset}USDT"
    
    try:
        url = "https://api.binance.com/api/v3/aggTrades"
        resp = requests.get(url, params={"symbol": binance_sym, "limit": 1000}, timeout=3)
        if resp.status_code == 200:
            trades = resp.json()
            if not trades: return None
            
            vol_by_price = {}
            for t in trades:
                price = float(t['p'])
                if price > 100: price = round(price)
                elif price > 1: price = round(price, 2)
                else: price = round(price, 4)
                
                qty = float(t['q'])
                is_buyer_maker = t['m'] # True=Venta Market, False=Compra Market
                
                if price not in vol_by_price:
                    vol_by_price[price] = {"buy_vol": 0, "sell_vol": 0, "total": 0}
                    
                if is_buyer_maker:
                    vol_by_price[price]["sell_vol"] += qty
                else:
                    vol_by_price[price]["buy_vol"] += qty
                    
                vol_by_price[price]["total"] += qty
                
            # POC (Point of Control)
            poc_price = max(vol_by_price.keys(), key=lambda p: vol_by_price[p]["total"])
            poc_data = vol_by_price[poc_price]
            delta_poc = poc_data["buy_vol"] - poc_data["sell_vol"]
            
            return {
                "POC_Price": poc_price,
                "POC_Total_Vol": round(poc_data["total"], 2),
                "POC_Buy_Vol": round(poc_data["buy_vol"], 2),
                "POC_Sell_Vol": round(poc_data["sell_vol"], 2),
                "POC_Delta": round(delta_poc, 2)
            }
    except Exception as e:
        logger.warning(f"Error fetching Footprint de {binance_sym}: {e}")
    return None

def obtener_long_short_ratio(simbolo: str, intervalo: str = "1h") -> dict:
    """Extrae el Top Trader Long/Short Account Ratio de Binance Futures para mapear apalancamiento."""
    sym = simbolo.upper()
    if not (sym.endswith("-USD") or sym.endswith("USDT") or sym == "BTC" or sym == "ETH"):
        return None
        
    base_asset = sym.replace("-USD", "").replace("USDT", "")
    binance_sym = f"{base_asset}USDT"
    
    # Mapeo conservador a periodos soportados por Binance Futures
    period_map = {"1m": "5m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    period = period_map.get(intervalo, "1h")
    
    try:
        url = "https://fapi.binance.com/fapi/v1/topLongShortAccountRatio"
        resp = requests.get(url, params={"symbol": binance_sym, "period": period, "limit": 1}, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                latest = data[0]
                return {
                    "Ratio": float(latest.get("longShortRatio", 1.0)),
                    "Long_Pct": float(latest.get("longAccount", 0.5)) * 100,
                    "Short_Pct": float(latest.get("shortAccount", 0.5)) * 100
                }
    except Exception as e:
        logger.warning(f"Error fetching Long/Short Ratio de {binance_sym}: {e}")
    return None

def fetch_bybit_klines(simbolo: str, intervalo: str, limit: int = 200) -> pd.DataFrame:
    import requests
    import pandas as pd
    
    interval_map = {
        "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
        "1d": "D", "1w": "W", "1M": "M"
    }
    
    bybit_interval = interval_map.get(intervalo, "60")
    
    sym = simbolo.upper().replace("-USD", "USDT")
    if not sym.endswith("USDT"):
        sym += "USDT"
        
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": sym, "interval": bybit_interval, "limit": limit}
    
    try:
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get('retCode') == 0:
                lst = data.get('result', {}).get('list', [])
                if not lst: return pd.DataFrame()
                
                df = pd.DataFrame(lst, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'Turnover'])
                df['timestamp'] = pd.to_numeric(df['timestamp'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                df = df[::-1] 
                
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    df[col] = pd.to_numeric(df[col])
                return df
        return pd.DataFrame()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

def obtener_correlacion_con_btc(df_asset: 'pd.DataFrame', intervalo: str) -> float:
    """Calcula la correlación estadística de Pearson del precio de cierre versus BTC."""
    import pandas as pd
    try:
        if df_asset is None or df_asset.empty: return 0.0
        df_btc = fetch_bybit_klines('BTCUSDT', intervalo, limit=len(df_asset))
        if df_btc is None or df_btc.empty: return 0.0
        # Alinear ambos dataframes por timestamp para garantizar cruce exacto
        df_align = pd.concat([df_asset['Close'], df_btc['Close']], axis=1, join='inner')
        if df_align.empty: return 0.0
        corr = df_align.iloc[:,0].corr(df_align.iloc[:,1])
        return float(corr) if not pd.isna(corr) else 0.0
    except:
        return 0.0

def analizar_mercado(simbolo_input: str, intervalo: str = "1d", use_gemini: bool = True) -> dict:
    """
    Analiza un símbolo.
    Calcula técnica y usa un LLM profesional (Gemini) para interpretar fundamentales y recomendar qué trade tomar.
    """
    simbolo = map_symbol(simbolo_input)
    kelly_pct = 0.015 # Defecto base a salvo
    
    # --- V18: LECTURA GENÉTICA DE SETTINGS.JSON ---
    import json
    import os
    
    dyn_ema_s = EMA_SHORT_PERIOD
    dyn_ema_l = EMA_LONG_PERIOD
    dyn_adx_t = ADX_THRESHOLD
    dyn_rsi_b = RSI_BUY_MAX
    dyn_rsi_s = RSI_SELL_MIN
    dyn_atr_sl = 1.0  # Tighter Stop Loss para Scalping/MR
    dyn_atr_tp = 1.5  # Tighter Take Profit para cazar el rebote estadístico rápido
    
    try:
        if os.path.exists("settings_v2.json"):
            with open("settings_v2.json", "r") as f:
                settings_db = json.load(f)
                if simbolo in settings_db:
                    cfg = settings_db[simbolo]
                    dyn_ema_s = cfg.get("EMA_SHORT", dyn_ema_s)
                    dyn_ema_l = cfg.get("EMA_LONG", dyn_ema_l)
                    dyn_adx_t = cfg.get("ADX_THRESH", dyn_adx_t)
                    dyn_rsi_b = cfg.get("RSI_BUY_MAX", dyn_rsi_b)
                    dyn_rsi_s = cfg.get("RSI_SELL_MIN", dyn_rsi_s)
                    dyn_atr_sl = float(cfg.get("ATR_SL_MULT", dyn_atr_sl))
                    dyn_atr_tp = float(cfg.get("ATR_TP_MULT", dyn_atr_tp))
    except Exception as e:
        logger.warning(f"Error leyendo settings genéticos para {simbolo}: {e}")
    # ----------------------------------------------
    
    try:
        # Determinar periodo/limite para Bybit
        is_scalping = False
        limit_velas = 200
        if intervalo == "1d":
            limit_velas = 180
        elif intervalo == "1h":
            limit_velas = 720
        elif intervalo == "15m":
            limit_velas = 500
        elif intervalo in ["1m", "5m"]:
            limit_velas = 500
            is_scalping = True

        # 1. Obtener datos históricos nativos de Bybit V5
        df = fetch_bybit_klines(simbolo, intervalo, limit=limit_velas)
        
        if df.empty or len(df) < 50:
            return {"Error": f"No se pudieron obtener suficientes datos K-Lines de Bybit para {simbolo}."}

        # Calcular Correlación de Pearson respecto al líder (BTC)
        corr_btc = obtener_correlacion_con_btc(df, intervalo)

        # Extraer Noticias solo si es necesario
        news_texts = "Noticias no requeridas en escaneo rápido."
        if use_gemini:
            # Puedes reimplementar un fetch de noticias RSS externo aquí o mantener el oráculo social
            pass

        # 2. Calcular los indicadores usando pandas_ta
        df.ta.ema(length=dyn_ema_s, append=True)
        df.ta.ema(length=dyn_ema_l, append=True)
        df.ta.ema(length=200, append=True)
        df.ta.adx(length=14, append=True)
        df.ta.rsi(length=14, append=True)
        df.ta.atr(length=14, append=True)

        try:
            df.ta.vwap(append=True)
        except Exception:
            pass

        latest = df.iloc[-1]
        
        # Determinar el nombre correcto de las columnas, en pandas_ta el ATR suele llamarse ATRr_14
        atr_col = [col for col in df.columns if col.startswith('ATR_') or col.startswith('ATRr_')]
        atr_key = atr_col[0] if atr_col else 'ATRr_14'
        vwap_col = [col for col in df.columns if col.startswith('VWAP')]
        vwap_val = latest[vwap_col[0]] if vwap_col and not pd.isna(latest.get(vwap_col[0])) else None
        
        ema_short_col = f"EMA_{dyn_ema_s}"
        ema_long_col = f"EMA_{dyn_ema_l}"
        
        if pd.isna(latest.get(ema_short_col)) or pd.isna(latest.get(ema_long_col)) or pd.isna(latest.get('ADX_14')) or pd.isna(latest.get('RSI_14')) or pd.isna(latest.get(atr_key)):
            return {"Error": "Datos insuficientes para calcular indicadores en la última vela."}

        ema_short_val = latest[ema_short_col]
        ema_long_val = latest[ema_long_col]
        adx = latest['ADX_14']
        rsi = latest['RSI_14']
        atr = latest[atr_key]
        current_price = latest['Close']
        ema200_val = latest.get('EMA_200', current_price)
        
        df['ATR_mean'] = df[atr_key].rolling(50).mean()
        df['ATR_std'] = df[atr_key].rolling(50).std()
        df['ATR_Z'] = (df[atr_key] - df['ATR_mean']) / df['ATR_std']
        atr_z_val = df['ATR_Z'].iloc[-1]
        atr_z = float(atr_z_val) if not pd.isna(atr_z_val) else 0.0
        
        if atr_z > 3.0:
            regime = "⚠️ EXPLOSIVO (CISNE NEGRO / LIQUIDITY SQUEEZE)"
        elif atr_z > 1.5:
            regime = "⚡ ALTA VOLATILIDAD (TRENDING)"
        else:
            regime = "🧘 NORMAL (CALM)"

        # --- V20: CONFLUENCIA FRACTAL 3D (MICRO + MACRO) ---
        tendencia_macro = "NEUTRAL"
        tendencia_micro = "NEUTRAL"
        
        try:
            # 1. MACRO TREND (4H) - Perspectiva Institucional
            df_macro = fetch_bybit_klines(simbolo, "240", limit=200)
            if not df_macro.empty and len(df_macro) > 21:
                df_macro.ta.ema(length=9, append=True)
                df_macro.ta.ema(length=21, append=True)
                ultimo_macro = df_macro.iloc[-1]
                ema9_m = ultimo_macro.get('EMA_9')
                ema21_m = ultimo_macro.get('EMA_21')
                if not pd.isna(ema9_m) and not pd.isna(ema21_m):
                    if ema9_m > ema21_m: tendencia_macro = "ALCISTA"
                    elif ema9_m < ema21_m: tendencia_macro = "BAJISTA"
                    
            # 2. MICRO TREND (5M) - Entrada Sniper
            df_micro = fetch_bybit_klines(simbolo, "5", limit=200)
            if not df_micro.empty and len(df_micro) > 21:
                df_micro.ta.ema(length=9, append=True)
                df_micro.ta.ema(length=21, append=True)
                ultimo_micro = df_micro.iloc[-1]
                ema9_u = ultimo_micro.get('EMA_9')
                ema21_u = ultimo_micro.get('EMA_21')
                if not pd.isna(ema9_u) and not pd.isna(ema21_u):
                    if ema9_u > ema21_u: tendencia_micro = "ALCISTA"
                    elif ema9_u < ema21_u: tendencia_micro = "BAJISTA"
        except Exception as e:
            logger.warning(f"Error extrayendo Confluencia Fractal para {simbolo}: {e}")

        # 3. Lógica Técnica Base + V6 SMC (Smart Money Concepts)
        signal = "ESPERAR"
        
        # --- ALERTA MACRO TEMPRANA (Dólar DXY) ---
        dxy_spike = False
        try:
            import yfinance as yf
            dxy_data = yf.download("DX-Y.NYB", period="1d", interval="15m", progress=False)
            if not dxy_data.empty and len(dxy_data) >= 2:
                # En pandas_series con multindex a veces hay que extraerlo
                dxy_serie = dxy_data['Close'] if isinstance(dxy_data['Close'], pd.Series) else dxy_data['Close'].iloc[:,0]
                dxy_pct_change = float(dxy_serie.pct_change().iloc[-1])
                if dxy_pct_change > 0.0015:  # Spike > 0.15% en el Dolar en solo 15 min es letal
                    dxy_spike = True
                    logger.warning(f"[{simbolo}] 🌍 ALERTA MACRO: Salto violento del Dólar (DXY +{dxy_pct_change*100:.2f}%). Peligro sistémico para Crypto.")
        except Exception as e:
            pass
        
        # --- NUEVO CÁLCULO MICRO-ESTRUCTURA SMC (FVG & WICKS) ---
        smc_micro_text = "Estructura Típica (Sin alteraciones anómalas a corto plazo)."
        fvg_bull = False
        fvg_bear = False
        ob_bull = False
        ob_bear = False
        
        try:
            # FVG (Fair Value Gap) de las últimas velas:
            if df['Low'].iloc[-1] > df['High'].iloc[-3]:
                fvg_bull = True
                smc_micro_text = "🟢 Bullish FVG Inminente (Imbalance dejado atrás)."
            elif df['High'].iloc[-1] < df['Low'].iloc[-3]:
                fvg_bear = True
                smc_micro_text = "🔴 Bearish FVG Inminente (Vacío estructural bajista)."

            # Detección de rechazos HFT por Mechas (Wicks) en las ultimas 3 velas
            for i in range(-3, 0):
                c = df.iloc[i]
                c_open = c['Open']
                c_close = c['Close']
                c_high = c['High']
                c_low = c['Low']
                body = abs(c_open - c_close)
                if body == 0: body = 0.0001
                
                lower_wick = min(c_open, c_close) - c_low
                upper_wick = c_high - max(c_open, c_close)
                
                # Criterio de Fuerte Rechazo (Liquidity Sweep)
                if lower_wick > 2.5 * body:
                    ob_bull = True
                    smc_micro_text += " | ⚠️ PELIGRO SHORTS: Rechazo agresivo del fondo (Larga Mecha Inferior/Hammer)."
                elif upper_wick > 2.5 * body:
                    ob_bear = True
                    smc_micro_text += " | ⚠️ PELIGRO LONGS: Rechazo fuerte desde el tope (Larga Mecha Superior/Shooting Star)."
                    
                    
        except Exception as e:
            logger.warning(f"Error analizando SMC Micro-Estructura en {simbolo}: {e}")
            
        # --- VIBRACIÓN DE BARRIDOS INSTITUCIONALES (SWEEP DETECTOR) ---
        try:
            import sweep_detector
            sweep_status = sweep_detector.detect_sweep(df, window=20)
            if sweep_status == "BULLISH_SWEEP":
                smc_micro_text += " | 🚨 BULLISH SWEEP DETECTADO: Trampa bajista frustrada, buscando LONG."
                ob_bull = True # Forzamos que lo trate como fuerte defensa alcista
            elif sweep_status == "BEARISH_SWEEP":
                smc_micro_text += " | 🚨 BEARISH SWEEP DETECTADO: Trampa alcista frustrada, buscando SHORT."
                ob_bear = True # Forzamos defensa bajista
        except Exception as e:
            pass
        # -------------------------------------------------------------
        
        adx_aprobado = adx > dyn_adx_t
        smc_bullish_fuerte = fvg_bull or ob_bull
        smc_bearish_fuerte = fvg_bear or ob_bear

        # COMPRAS (LONG)
        if ema_short_val > ema_long_val and rsi < dyn_rsi_b:
            if adx_aprobado or smc_bullish_fuerte:
                if ob_bear or fvg_bear:
                    # Choque contra Liquidez Vendedora SMC -> Abortar
                    logger.info(f"[{simbolo}] V6 COMPRA RECHAZADA POR SMC (Choque con FVG/OB Bajista Institucional).")
                elif dxy_spike:
                    logger.critical(f"[{simbolo}] V6 COMPRA RECHAZADA POR MACRO: Salto violento del Dólar (DXY) activo. No arriesgaremos fiat.")
                elif intervalo == "5m" and not fvg_bull:
                    # RESTRICCIÓN GENÉTICA DE 5 MINUTOS: SOLO ENTRA SI HAY FVG
                    logger.info(f"[{simbolo}] 🧬 V4 COMPRA RECHAZADA GENÉTICA (Req FVG = 1.0 en 5m). FVG Ausente.")
                elif tendencia_macro in ["ALCISTA", "NEUTRAL"] and tendencia_micro in ["ALCISTA", "NEUTRAL"]:
                    signal = "COMPRA"
                else:
                    logger.info(f"[{simbolo}] V20 COMPRA FRACTAL RECHAZADA: Macro={tendencia_macro}, Micro={tendencia_micro}")

        # VENTAS (SHORT)
        elif ema_short_val < ema_long_val and rsi > dyn_rsi_s:
            if adx_aprobado or smc_bearish_fuerte:
                if ob_bull or fvg_bull:
                    # Choque contra Liquidez Compradora SMC -> Abortar
                    logger.info(f"[{simbolo}] V6 VENTA RECHAZADA POR SMC (Choque con FVG/OB Alcista Institucional).")
                elif intervalo == "5m" and not fvg_bear:
                    # RESTRICCIÓN GENÉTICA DE 5 MINUTOS: SOLO ENTRA SI HAY FVG
                    logger.info(f"[{simbolo}] 🧬 V4 VENTA RECHAZADA GENÉTICA (Req FVG = 1.0 en 5m). FVG Ausente.")
                elif tendencia_macro in ["BAJISTA", "NEUTRAL"] and tendencia_micro in ["BAJISTA", "NEUTRAL"]:
                    signal = "VENTA"
                else:
                    logger.info(f"[{simbolo}] V20 VENTA FRACTAL RECHAZADA: Macro={tendencia_macro}, Micro={tendencia_micro}")

        # --- V15: HARD FILTER MACHINE LEARNING (Random Forest) ---
        ml_pred = predecir_direccion_ml(df)
        if signal in ["COMPRA", "VENTA"] and ml_pred:
            prob_alcista = ml_pred.get("Probabilidad_Alcista", 0)
            prob_bajista = ml_pred.get("Probabilidad_Bajista", 0)
            if signal == "COMPRA" and prob_alcista < 60:
                logger.info(f"[{simbolo}] COMPRA BLOQUEADA POR ML: Prob Alcista {prob_alcista:.1f}% < 60%")
                signal = "ESPERAR"
            elif signal == "VENTA" and prob_bajista < 60:
                logger.info(f"[{simbolo}] VENTA BLOQUEADA POR ML: Prob Bajista {prob_bajista:.1f}% < 60%")
                signal = "ESPERAR"
        # ---------------------------------------------------------

        # --- V17: HARD FILTER ORDER FLOW (CVD) ---
        cvd_data = obtener_cvd_binance(simbolo, intervalo)
        if signal in ["COMPRA", "VENTA"] and cvd_data is not None:
            if signal == "COMPRA" and cvd_data < 0:
                logger.info(f"[{simbolo}] COMPRA BLOQUEADA POR CVD: Falsa subida. Mercado agresivo inyectando Shorts (CVD {cvd_data}).")
                signal = "ESPERAR"
            elif signal == "VENTA" and cvd_data > 0:
                logger.info(f"[{simbolo}] VENTA BLOQUEADA POR CVD: Falsa bajada. Mercado agresivo inyectando Longs (CVD {cvd_data}).")
                signal = "ESPERAR"
        # ---------------------------------------------------------

        # --- V22: ORÁCULO DE PÁNICO SOCIAL (NLP SENTIMENT) ---
        social_data = None
        if signal in ["COMPRA", "VENTA"]:
            try:
                import social_oracle_v2
                social_data = social_oracle_v2.evaluar_panico_masas(simbolo)
                if social_data:
                    sn_score = social_data.get("Score", 0)
                    if signal == "COMPRA" and sn_score <= -7:
                        logger.warning(f"[{simbolo}] COMPRA BLOQUEADA POR FUD SOCIAL V22: Score {sn_score}/10 -> {social_data.get('Veredicto', '')}")
                        signal = "ESPERAR"
                    elif signal == "VENTA" and sn_score >= 7:
                        logger.warning(f"[{simbolo}] VENTA BLOQUEADA POR FOMO SOCIAL V22: Score {sn_score}/10 -> {social_data.get('Veredicto', '')}")
                        signal = "ESPERAR"
            except Exception as e:
                logger.warning(f"Error invocando Oráculo Social V22: {e}")
        # ---------------------------------------------------------

        # --- V23: DYNAMIC FEEDBACK LOOP (APRENDIZAJE DE ERRORES) ---
        if signal in ["COMPRA", "VENTA"]:
            try:
                import feedback_loop_v1
                estado_salud = feedback_loop_v1.evaluar_estado_supervivencia(simbolo)
                if estado_salud.get("Trauma_Simbolo"):
                    logger.critical(f"[{simbolo}] {signal} BLOQUEADA POR FEEDBACK LOOP: El símbolo está vetado por stop losses consecutivos.")
                    signal = "ESPERAR"
                elif estado_salud.get("Trauma_Global"):
                    # En modo trauma global, exigimos más precisión de la IA
                    wr_msg = estado_salud.get('Win_Rate_Global', 0)
                    logger.critical(f"[{simbolo}] TRAUMA GLOBAL DETECTADO (Win Rate Reciente: {wr_msg}%): El bot elevando exigencia matemática al 80%.")
                    if 'ml_pred' in locals() and ml_pred:
                        prob_a = ml_pred.get("Probabilidad_Alcista", 0)
                        prob_b = ml_pred.get("Probabilidad_Bajista", 0)
                        if signal == "COMPRA" and prob_a < 80:
                            logger.critical(f"[{simbolo}] CANCELADO: Trauma global activo, se exige 80% WinProb pero tenemos {prob_a:.1f}%")
                            signal = "ESPERAR"
                        elif signal == "VENTA" and prob_b < 80:
                            logger.critical(f"[{simbolo}] CANCELADO: Trauma global activo, se exige 80% WinProb pero tenemos {prob_b:.1f}%")
                            signal = "ESPERAR"
                    else:
                        logger.critical(f"[{simbolo}] CANCELADO ESTRICTAMENTE: Trauma global activo y predictor ML apagado. Entrar a ciegas es suicidio.")
                        signal = "ESPERAR"
            except Exception as e:
                logger.warning(f"Error invocando Feedback Loop V23: {e}")
        # ---------------------------------------------------------
        
        # 4. Obtener el Análisis Profesional de la IA usando las noticias y la técnica
        ob_data = obtener_order_book(simbolo)
        footprint_data = obtener_footprint(simbolo)
        funding_rate = obtener_funding_rate(simbolo)
        fng_index = obtener_fear_and_greed()
        ls_ratio = obtener_long_short_ratio(simbolo, intervalo)
        titulares_noticias = news_oracle.obtener_titulares_macro(simbolo)
        ob_text = ""
        if ob_data:
            exchs = ob_data.get('Exchanges_Agregados', 1)
            cvd_str = f"| CVD (Últ. 50 vel): {cvd_data} " if cvd_data is not None else ""
            ob_text = (
                f"Macro Order Book Agregado ({exchs} Exchanges) - Profundidad Nivel 2 {cvd_str}:\n"
                f"- Volumen Total Bids (Compra): {ob_data['Presion_Compra']} vs Volumen Total Asks (Venta): {ob_data['Presion_Venta']}\n"
                f"- Muro Global de Compra masivo detectado en: {ob_data['Muro_Compra']['Precio']}\n"
                f"- Muro Global de Venta masivo detectado en: {ob_data['Muro_Venta']['Precio']}\n"
            )
            
        footprint_text = ""
        if footprint_data:
            footprint_text = (
                f"\nMicro-Estructura (Footprint / Order Flow Últimos 1000 Trades):\n"
                f"- Point of Control (POC): ${footprint_data['POC_Price']} (Nivel con Mayor Volumen)\n"
                f"- Volumen Operado en POC: {footprint_data['POC_Total_Vol']}\n"
                f"- Delta en POC: {footprint_data['POC_Delta']} (Positivo=Agresión de Compra, Negativo=Agresión de Venta)\n"
            )
            
        contexto_macro = ""
        if fng_index or funding_rate or ls_ratio:
            contexto_macro = "Contexto Macro, Apalancamiento y Sentimiento:\n"
            if fng_index: contexto_macro += f"- Crypto Fear & Greed Index: {fng_index}\n"
            if funding_rate: contexto_macro += f"- Binance Futures Funding Rate: {funding_rate}\n"
            if ls_ratio: contexto_macro += f"- Top Trader Long/Short Ratio: {ls_ratio['Ratio']:.2f} ({ls_ratio['Long_Pct']:.1f}% Longs vs {ls_ratio['Short_Pct']:.1f}% Shorts)\n"
        
        # ORÁCULO SOCIAL: Reemplaza las noticias genéricas
        try:
            titulares_sociales = asyncio.run(SocialOracle().fetch_latest_sentiment(simbolo))
        except Exception as e:
            titulares_sociales = "Sin red de oráculo social."
            
        ballenas = obtener_actividad_ballenas(simbolo, umbral_usd=100000.0)
        texto_ballenas = ""
        if ballenas.get("Estado") == "Activo":
            texto_ballenas = (
                f"\n🐋 RASTREO ON-CHAIN DE BALLENAS (Traders Agresivos > $100k USD en Vivo):\n"
                f"- Ataques Institucionales (Market Orders): {ballenas['Total_Ballenas_Detectadas']}\n"
                f"- Volumen inyectado al Alza (Market Longs): ${ballenas['Volumen_Longs_Superiores']:,.2f}\n"
                f"- Volumen inyectado a la Baja (Market Shorts): ${ballenas['Volumen_Shorts_Superiores']:,.2f}\n"
                f"- Dominancia Actual de Ballenas: {ballenas['Dominancia']}\n"
            )
            
        contexto_macro += (
            texto_ballenas +
            f"\n📊 RÉGIMEN DE ESTRUCTURA DE MERCADO:\n"
            f"- Estado: {regime}\n"
            f"- Volatilidad (Z-Score ATR): {atr_z:.2f} (Si Z > 3.0, es un Cisne Negro. NO operes patrones técnicos normales, debes huir rápidamente).\n"
            f"\n📰 ORÁCULO SOCIAL CANSADO DE ESPERAR (Alternative Data - Foco en Retail/FUD):\n{titulares_sociales}\n"
            f"(Analiza estrictamente si este sentimiento destruye la tesis técnica. La noticia MATA al gráfico. ¡Si es apocalíptico el FOMO o FUD, aborta o apalanca en esa dirección!)\n"
        )

        ml_text = ""
        if ml_pred:
            dir_label = ml_pred.get("Clasificacion", "")
            prob_alcista = ml_pred.get("Probabilidad_Alcista", 0)
            prob_bajista = ml_pred.get("Probabilidad_Bajista", 0)
            ml_text = (
                f"\n[CRÍTICO] Motor de Predicción Densa (Machine Learning - Árbol de Decisión):\n"
                f"- Score Estadístico de Cierre Alcista: {prob_alcista:.2f}%\n"
                f"- Score Estadístico de Cierre Bajista: {prob_bajista:.2f}%\n"
                f"- Veredicto Matemático: {dir_label}\n"
                f"- Indicador Más Pesado en el Modelo Computacional: {ml_pred.get('Top_Feature', 'N/A')}\n"
            )

        # 4. Obtener el Análisis Profesional de la IA (COMITÉ MULTI-AGENTE V24)
        ai_response_text = ""
        comite_data = None
        if GEMINI_API_KEY and use_gemini:
            try:
                import multi_agent_engine
                
                # Empaquetado Especializado para las 3 Mentes
                data_tecnica = f"Precio: {current_price:.4f}, Signal Matemática: {signal}\n{ob_text}{footprint_text}{ml_text}"
                data_riesgo = f"ATR: {atr:.4f}\nZ-Score Volatilidad: {atr_z:.2f}\nKelly Criterion Base: {kelly_pct*100:.2f}%\n"
                tit_txt = titulares_sociales if 'titulares_sociales' in locals() else 'N/A'
                data_macro = f"Titulares Globales Vivos:\n{tit_txt}\n\nContexto (Fear & Greed / On-Chain):\n{contexto_macro}"
                
                consenso = multi_agent_engine.obtener_consenso_comite(data_tecnica, data_riesgo, data_macro, simbolo)
                comite_data = consenso
                
                if consenso.get("Consenso_Alcanzado", False):
                    signal = consenso.get("Decision_Final", signal)
                    logger.info(f"[{simbolo}] V24: COMITÉ MULTI-AGENTE APROBÓ EL TRADE: {signal}")
                else:
                    if signal in ["COMPRA", "VENTA"]:
                        logger.warning(f"[{simbolo}] V24: TRADE DESTRUCTIVO VETADO POR EL COMITÉ.")
                        signal = "ESPERAR"
                        
                ai_response_text = "Comité Cuántico V24 evaluado exitosamente."
            except Exception as e:
                logger.error(f"Error invocando Comité Multi-Agente: {e}")
                ai_response_text = "Error Crítico Neural."
        else:
            ai_response_text = "⚠️ Integración de IA apagada."

        # ===== 5. FASE V26: INTERVENCIÓN DE REINFORCEMENT LEARNING (Q-LEARNING) =====
        # Discretizar el Entorno (State) para la Tabla-Q
        rsi_state = "OVERSOLD" if rsi < 30 else "OVERBOUGHT" if rsi > 70 else "NEUTRAL"
        trend_state = "STRONG" if adx > 25 else "WEAK"
        estado_rl = f"{simbolo}_Tend:{trend_state}_RSI:{rsi_state}_Reg:{regime}"
        
        # El cerebro RL decide si confía en el algoritmo (Explotar) o mutar (Explorar).
        recomendacion_algoritmica = "LONG" if signal == "COMPRA" else "SHORT" if signal == "VENTA" else "WAIT"
        accion_rl = rl_engine.select_action(state=estado_rl, recommended_action=recomendacion_algoritmica)
        
        if accion_rl != recomendacion_algoritmica:
            logger.warning(f"🤯 [RL MUTACIÓN] El Agente Autónomo (Q-Learning) rechazó '{recomendacion_algoritmica}' y tomó '{accion_rl}' basado en dolor histórico.")
            signal = "COMPRA" if accion_rl == "LONG" else "VENTA" if accion_rl == "SHORT" else "ESPERAR"

        # --- V40: GARCH PREDICTIVE VOLATILITY ENGINE ---
        import garch_engine
        garch_data = garch_engine.predecir_volatilidad_garch(simbolo, intervalo=intervalo)
        
        # Niveles de Riesgo Base descubiertos por Enjambre Genético
        base_sl_mult = dyn_atr_sl
        base_tp_mult = dyn_atr_tp
        
        if garch_data.get("Estado") == "OK":
            vol_multiplier = garch_data.get("Múltiplo_Volatilidad", 1.0)
            sl_mult = base_sl_mult * vol_multiplier
            tp_mult = base_tp_mult * vol_multiplier
            
            # CAPPING DE EMERGENCIA (Solicitud de Strict Scalping):
            # No permitimos que GARCH aleje el SL más allá de 1.1 ATR ni el TP más allá de 1.6 ATR.
            sl_mult = min(sl_mult, 1.1)
            tp_mult = min(tp_mult, 1.6)
            
            logger.info(f"[{simbolo}] V40 GARCH {garch_data.get('Tendencia')}: Multiplicador Riesgo x{vol_multiplier:.2f} (Limites Aplicados SL={sl_mult:.2f}, TP={tp_mult:.2f})")
        else:
            sl_mult = base_sl_mult
            tp_mult = base_tp_mult
        # -----------------------------------------------

        # Niveles para LONG
        long_entry = current_price
        # Limite MAX: 0.8% de pérdida
        calc_long_sl = current_price - (sl_mult * atr)
        min_allowed_long_sl = current_price * (1.0 - 0.008)
        long_sl = max(calc_long_sl, min_allowed_long_sl)
        
        # Limite MAX: 1.2% de ganancia
        calc_long_tp = current_price + (tp_mult * atr)
        max_allowed_long_tp = current_price * (1.0 + 0.012)
        long_tp = min(calc_long_tp, max_allowed_long_tp)
        
        # Niveles para SHORT
        short_entry = current_price
        calc_short_sl = current_price + (sl_mult * atr)
        max_allowed_short_sl = current_price * (1.0 + 0.008)
        short_sl = min(calc_short_sl, max_allowed_short_sl)
        
        calc_short_tp = current_price - (tp_mult * atr)
        min_allowed_short_tp = current_price * (1.0 - 0.012)
        short_tp = max(calc_short_tp, min_allowed_short_tp)

        # --- V38: FRACTIONAL KELLY CRITERION + MARKOV HMM ---
        kelly_pct = 0.02 # Base 2%
        
        regimen_str, is_choppy = hmm_engine.interrogar_markov(simbolo)
        regime = f"{regime} | HMM: {regimen_str}"  # Anexamos Markov al ADX

        # V6.4 Market Cache UPDATE (Moviéndolo aquí para asegurar guardado sin importar el modo Scalp/Swing)
        try:
            import json, datetime
            global QUANT_MARKET_CACHE
            if 'QUANT_MARKET_CACHE' not in globals():
                QUANT_MARKET_CACHE = {}
                
            QUANT_MARKET_CACHE[simbolo] = {
                "Time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "Price": current_price,
                "Trend_4H": regime,
                "RSI_15m": rsi if 'rsi' in locals() else 50,
                "ADX": adx,
                "Signal_15m": signal,
                "FVG_Context": "GARCH",
                "Corr_BTC": corr_btc,
                "Funding": funding_rate if funding_rate else 0.0,
                "POC_Delta": footprint_data.get('POC_Delta', "N/A") if footprint_data else "N/A"
            }
            with open("quant_market_cache.json", "w") as f_json:
                json.dump(QUANT_MARKET_CACHE, f_json)
        except Exception as e:
            logger.error(f"Cache error: {e}")

        # --- CIRCUIT BREAKER DE VOLATILIDAD EXTREMA ---
        if "Violento" in regimen_str:
            logger.warning(f"[{simbolo}] ⚠️ CIRCUIT BREAKER ACTIVADO: Choque Direccional Violento (Cisne Negro). Suspendiendo todas las redes y operativas.")
            return None

        # --- BIFURCACIÓN DUAL REGIME HMM (GRID MAKER V2.0) ---
        kelly_pct = 0.015 # Defecto agresivo moderado
        
        if is_choppy or ('estado_rl' in locals() and 'GRID_MODE' in estado_rl):
            import hmm_dual_grid
            logger.info(f"[{simbolo}] HMM Choppy Detected. Transicionando a HMM DUAL GRID...")
            
            # Espaciado dinámico adaptado a la volatilidad histórica de cada activo
            grid_spacing = 0.0037
            if "BNB" in simbolo:
                grid_spacing = 0.0018  # BNB es mucho menos volátil que BTC, requiere red más apretada
            elif "ETH" in simbolo:
                grid_spacing = 0.0028
                
            hmm_state_grid = 1 # Malla Dual por defecto
            if float(current_price) < float(ema200_val):
                # SMART DCA REPLICADO DE BYBIT MAINNET:
                # Si el precio sangró debajo de EMA200 pero ya hizo piso (HMM = Choppy),
                # no anularemos los longs. Activamos malla DUAL para promediar y comprar barato.
                hmm_state_grid = 1 
                logger.info(f"[{simbolo}] 🛡️ SMART DCA (Piso Detectado): Precio bajo EMA200 ({ema200_val:.2f}) pero lateral. Malla DUAL reactivada para promediar a mejor precio.")
            elif float(current_price) > float(ema200_val):
                hmm_state_grid = 1 # Habilitar ambas direcciones.
                logger.info(f"[{simbolo}] 📈 BULL RANGE: Lateral sobre EMA200 ({ema200_val:.2f}). Malla DUAL activada.")
                
            compras, ventas = hmm_dual_grid.calcular_dual_grid(float(current_price), grid_spacing, 10, hmm_state_grid)
            
            grid_report = {
                "Simbolo": simbolo,
                "Simbolo Original": simbolo_input,
                "Intervalo": "15m",
                "Signal": "GRID_DEPLOY",
                "Kelly_Risk_Pct": round(kelly_pct, 4),
                "Buys": compras,
                "Sells": ventas,
                "Dual_State": 1,
                "Social_Sentinel": "HMM Dual Bilateral",
                "Comite_Agentico": "Mercado lateralizado. Desplegando redes Maker bidireccionalmente.",
                "RL_State": "GRID_MODE",
                "AI_Analysis": "Escaneo Markov HMM ha invocado la Malla de Captura."
            }
            return grid_report
        else:
            # STATE 0 / 2: TENDENCIA EXPLOSIVA (CUT & RUN)
            # Pasamos la directiva al broker para que aniquile la malla contraria
            pass 
        # -------------------------------------
        
        try:
            if 'ml_pred' in locals() and ml_pred and signal in ["COMPRA", "VENTA"]:
                win_prob = ml_pred.get("Probabilidad_Alcista", 50) / 100.0 if signal == "COMPRA" else ml_pred.get("Probabilidad_Bajista", 50) / 100.0
                reward_to_risk = tp_mult / sl_mult # Ej: 1.5 / 1.0 = 1.5
                
                # Formula Kelly = W - ((1 - W) / R)
                kelly_raw = win_prob - ((1.0 - win_prob) / reward_to_risk)
                
                # Solicitud del usuario: Mantener Kelly en x2 (Agresivo Moderado) dado que reducirá capital desde exchange
                kelly_x3 = kelly_raw * 2.0
                
                # Penalización Markov: Si el Régimen es Tóxico, cortamos la exposición
                if is_choppy:
                    kelly_x3 /= 2.0
                    logger.warning(f"[{simbolo}] HMM Choppy Detected. Castigando Kelly a la mitad (Reducción Exposición).")
                    
                # Hard Limits Institucionales adaptados al Kelly Agresivo (Min 1.0%, Max 25.0% de Riesgo Total de Liquidación por trade)
                kelly_pct = max(0.010, min(kelly_x3, 0.25))
                
                logger.info(f"[{simbolo}] Kelly Pct Ajustado (Kellyx3): {kelly_pct*100:.2f}% (W={win_prob:.2f}, R={reward_to_risk:.2f}, RAW={kelly_raw:.3f})")
        except Exception as e:
            logger.warning(f"Error calculando Kelly/HMM Avanzado: {e}")
        # ---------------------------------------


        res = {
            "Simbolo": simbolo,
            "Simbolo Original": simbolo_input,
            "Intervalo": intervalo,
            "Signal": signal,
            "SMC_Micro": smc_micro_text,
            "Corr_BTC": round(corr_btc, 4),
            "Kelly_Risk_Pct": round(kelly_pct, 4),
            "Precio Actual": round(float(current_price), 4),
            "Social_Sentinel": social_data,
            "Comite_Agentico": comite_data,
            "RL_State": getattr(locals(), 'estado_rl', "UNKNOWN_STATE"),
            "AI_Analysis": ai_response_text,
            "Long": {
                "Entry": round(float(long_entry), 4),
                "SL": round(float(long_sl), 4),
                "TP": round(float(long_tp), 4)
            },
            "Short": {
                "Entry": round(float(short_entry), 4),
                "SL": round(float(short_sl), 4),
                "TP": round(float(short_tp), 4)
            },
            "EMA Short": round(float(ema_short_val), 4),
            "EMA Long": round(float(ema_long_val), 4),
            "EMA Short Period": EMA_SHORT_PERIOD,
            "EMA Long Period": EMA_LONG_PERIOD,
            "ADX": round(float(adx), 2),
            "RSI": round(float(rsi), 2),
            "ATR": round(float(atr), 4),
            "Regimen_Mercado": regime,
            "ATR_Z_Score": round(float(atr_z), 2) if not pd.isna(atr_z) else 0.0,
            "Titulares_Macro": titulares_noticias if 'titulares_noticias' in locals() else "Data Alternativa Offline"
        }
        if vwap_val:
            res["VWAP"] = round(float(vwap_val), 4)
        if 'ob_data' in locals() and ob_data:
            res["OrderBook"] = ob_data
            if 'cvd_data' in locals() and cvd_data is not None:
                res["OrderBook"]["CVD"] = cvd_data
                
        if 'fng_index' in locals() and fng_index:
            res["Fear_And_Greed"] = fng_index
        if 'ls_ratio' in locals() and ls_ratio:
            res["Long_Short_Ratio"] = ls_ratio
        if 'funding_rate' in locals() and funding_rate:
            res["Funding_Rate"] = funding_rate
        if 'footprint_data' in locals() and footprint_data:
            res["Footprint"] = footprint_data
        if 'ml_pred' in locals() and ml_pred:
            res["ML_Prediction"] = ml_pred
            
        return res
    except Exception as e:
        logger.error(f"Error analizando {simbolo}: {e}")
        return {"Error": str(e)}

def chatear_con_ia(texto: str, user_id: int) -> str:
    """
    Función para chatear con el bot de forma libre, manteniendo memoria de la conversación.
    """
    import json
    import os
    import datetime
    
    if not GEMINI_API_KEY:
        return "⚠️ La IA está apagada porque falta tu GEMINI_API_KEY."
        
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
        
    try:
        if user_id not in USER_MEMORIES:
            LIVE_MODE_ENV = os.environ.get("LIVE_MODE", "False").lower() == "true"
            env_name = "Bybit Live Matriz Institucional (Dinero Real)" if LIVE_MODE_ENV else "Binance Testnet (Dinero Simulacion)"
            
            USER_MEMORIES[user_id] = [
                {
                    "role": "user",
                    "parts": [{"text": f"Initiating API connection to Quant Analyzer backend. Environment: {env_name}."}]
                },
                {
                    "role": "model",
                    "parts": [{"text": f"API connection established. Awaiting market data for rulebook processing."}]
                }
            ]
            
        # --- FIX ALUCINACIÓN: INYECCIÓN DE CONTEXTO EN TIEMPO REAL (MULTI-NODO) ---
        sys_prompt = ""
        try:
            market_data_table = "DATOS DEL ESCÁNER CENTINELA NO DISPONIBLES."
            try:
                cache_bpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quant_market_cache.json")
                if os.path.exists(cache_bpath):
                    with open(cache_bpath, "r") as f:
                        cache = json.load(f)
                        
                    lines = ["Asset | Price | 4H Trend | 15m RSI | ADX | Corr vs BTC | Fund% | POC Delta(Footprint) | Setup"]
                    lines.append("---|---|---|---|---|---|---|---|---")
                    for sym, data in cache.items():
                        corr_val = data.get('Corr_BTC', 0.0)
                        funding = data.get('Funding', 0.0)
                        delta = data.get('POC_Delta', 'N/A')
                        lines.append(f"{sym} | ${data.get('Price',0):.2f} | {data.get('Trend_4H','')} | {data.get('RSI_15m',0):.1f} | {data.get('ADX',0):.1f} | {corr_val:.2f} | {funding} | {delta} | 15m Signal: {data.get('Signal_15m','WAIT')}")
                    market_data_table = "\n".join(lines)
            except Exception as e:
                logger.error(f"Error reading market cache: {e}")
            
            ACTIVE_BROKER = os.environ.get("ACTIVE_BROKER", "bybit").lower()
            LIVE_MODE_ENV = os.environ.get("LIVE_MODE", "False").lower() == "true"
            es_live = "Bybit Live" if ACTIVE_BROKER == "bybit" else "Binance Testnet"
            
            estado_trades = f"En este momento NO tienes operaciones abiertas en {es_live}."
            saldo_actual = "1000.00 USD"
            historial_pnl_str = ""
            
            try:
                if ACTIVE_BROKER == "bybit":
                    from pybit.unified_trading import HTTP
                    client = HTTP(testnet=not LIVE_MODE_ENV, api_key=os.environ.get('BYBIT_API_KEY'), api_secret=os.environ.get('BYBIT_SECRET'))
                    
                    # 1. Obtener Wallet Live REST
                    w_res = client.get_wallet_balance(accountType="UNIFIED")
                    if w_res.get('retCode') == 0:
                        bal = float(w_res["result"]["list"][0].get("totalWalletBalance", 1000.0))
                        saldo_actual = f"${bal:,.2f} USD LÍQUIDOS REALES (BYBIT REST)"
                    
                    # 2. Obtener Posiciones Abiertas Reales REST
                    p_res = client.get_positions(category="linear", settleCoin="USDT")
                    if p_res.get('retCode') == 0:
                        abiertas = [p for p in p_res["result"]["list"] if float(p.get("size", 0)) > 0]
                        if abiertas:
                            estado_trades = f"Tienes {len(abiertas)} operaciones ABIERTAS actualmente en {es_live}:\n"
                            for p in abiertas:
                                estado_trades += f"- {p['symbol']}: {p['side']} (Entrada: {p['avgPrice']} | Tamaño: {p['size']})\n"
                    
                    # 3. Obtener Últimas 5 Salidas Cerradas (Para la memoria pnl real)
                    import time
                    doce_hs = int((time.time() - 12 * 3600) * 1000)
                    c_res = client.get_closed_pnl(category="linear", limit=5, startTime=doce_hs)
                    if c_res.get('retCode') == 0 and c_res.get('result') and c_res['result'].get('list'):
                        historial_pnl_str = "\n--- HISTORIAL RECIENTE (ÚLTIMAS 5 OPERACIONES CERRADAS) ---\n"
                        for t in c_res['result']['list']:
                            pnl_val = float(t['closedPnl'])
                            emo = "✅ GANANCIA" if pnl_val > 0 else "❌ PÉRDIDA"
                            historial_pnl_str += f"{emo}: {t['symbol']} | PnL: ${pnl_val:.2f}\n"

                elif ACTIVE_BROKER == "binance":
                    try:
                        import broker_real_binance as bb
                        bal = bb.obtener_balance_usdt()
                        saldo_actual = f"${bal:,.2f} USD LÍQUIDOS (BINANCE)"
                        
                        pos = bb.client.account().get('positions', [])
                        abiertas = [p for p in pos if float(p.get("positionAmt", 0)) != 0]
                        if abiertas:
                            estado_trades = f"Tienes {len(abiertas)} operaciones ABIERTAS actualmente en BINANCE:\n"
                            for p in abiertas:
                                side = "LONG" if float(p.get("positionAmt", 0)) > 0 else "SHORT"
                                pnl = float(p.get("unrealizedProfit", 0))
                                estado_trades += f"- {p.get('symbol')}: {side} | PnL Flotante: ${pnl:.2f}\n"
                    except Exception as e_binance:
                        logger.error(f"Falla Binace en Chat: {e_binance}.")
            except Exception as e: 
                logger.error(f"Falla HTTP inyectando REST Saldo/Activos: {e}")
                saldo_actual = "$1,000.00 USD (FALLBACK LIMITADO)"
            
        except Exception as e:
            logger.warning(f"Error inyectando precios o trades generales: {e}")
        # GUARDAR EN MEMORIA PERENNE (RAG) Y RECUPERAR CONTEXTO ANTERIOR
        try:
            rag_engine.save_message(user_id, "user", texto)
            contexto_historico = rag_engine.search_similar_context(user_id, texto)
        except Exception as e:
            logger.error(f"Error en RAG Engine: {e}")
            contexto_historico = ""
            
        USER_MEMORIES[user_id].append({"role": "user", "parts": [{"text": texto}]})
        
        # Limitar memoria (ventana móvil) para evitar superar tokens de contexto de Gemini.
        # Conservamos siempre U0 y M0 (índices 0 y 1).
        # El último elemento siempre es 'user'.
        # Para que el empalme sea válido, el siguiente elemento tras M0 DEBE ser 'user'.
        # Es decir, la rebanada del final debe empezar con 'user' y terminar con 'user', por ende debe tener un número IMPAR de elementos.
        if len(USER_MEMORIES[user_id]) > 15:
            USER_MEMORIES[user_id] = USER_MEMORIES[user_id][:2] + USER_MEMORIES[user_id][-11:]
            
        # -- LISTA CASCADA DE SUPERVIVENCIA --
        # Si Google colapsa, probaremos el cluster entero uno por uno.
        # Si Google colapsa, probaremos el cluster entero uno por uno.
        model_cascade = [
            "gemini-2.5-pro",
            "gemini-2.0-pro-exp-02-05",
            "gemini-2.5-flash",
            "gemini-1.5-pro-latest"
        ]
        
        # CARGAR SUBCONSCIENTE (REFLEXIÓN NOCTURNA)
        subconsciente = ""
        try:
            if os.path.exists("quant_subconscious.json"):
                with open("quant_subconscious.json", "r", encoding="utf-8") as f:
                    sd = json.load(f)
                    subconsciente = sd.get("subconscious", "")
        except Exception as e:
            logger.warning(f"Error cargando subconsciente: {e}")
            
        # El contexto FVG está ahora fusionado dentro de la tabla del Snapshot
        fvg_context = "Ver tabla de activos para FVG Context y RSI."

        try:
            with open('quant_rule_book.json', 'r') as f:
                rb = json.load(f)
                rulebook_text = json.dumps(rb, indent=2)
        except:
            rulebook_text = "Sin reglas previas."

        instruccion_dios = (
            f"Eres Quant Pro 3.0, una Inteligencia Artificial Quant de Élite diseñada para dominar los criptomercados con precisión militar.\n"
            f"--- DIRECTRIZ DE PERSONALIDAD ---\n"
            f"Habla con autoridad, pero siempre dirígete al usuario exclusivamente como 'Jefe'. Eres su socio y asesor analítico de Wall Street. Tu tono debe ser dinámico, conversacional y sumamente interactivo, dejando de lado la robótica pesada.\n\n"
            f"--- DIRECTIVA DE ANÁLISIS INSTITUCIONAL ---\n"
            f"LA DATA DEL MERCADO ESTÁ ABAJO. Debes cruzarla contra el [RULEBOOK].\n"
            f"Tu objetivo final es ejecutar el [ejecutar_trade_real] si algún activo cumple las reglas. Si el mercado es basura hoy, díselo franca y abiertamente al Jefe.\n"
            f"NOTA CRÍTICA ON-CHAIN: El campo 'Corr vs BTC' es tu Pearson contra Bitcoin. Un valor > 0.8 significa sumisión total a BTC. 'Fund%' dicta el apalancamiento global. 'POC Delta' (Footprint) positivo indica agresión institucional de Compra, negativo Venta.\n"
            f"⚠️ DIRECTRIZ DE RIESGO SMC_MICRO: Observa siempre el campo SMC_Micro. Si dice 'PELIGRO SHORTS' o detecta 'FVGs bajistas inminentes', DEBES penalizar severamente la probabilidad matemática de Shortear.\n\n"
            f"--- FORMATO DE SALIDA ESTRICTO ---\n"
            f"Adopta exactamente esta estructura:\n"
            f"# 🔍 ANÁLISIS INSTITUCIONAL CUANTITATIVO\n"
            f"## 📊 ESTADO ACTUAL DEL MERCADO\n"
            f"(Viñetas con emojis como 💀, ⚡️, 🎯 detallando Volatilidad, Correlación, y Posiciones/Setup. Sé interactivo y explica el porqué brevemente)\n"
            f"## 🧠 PROBABILIDAD DE ÉXITO: [XX.X%]\n"
            f"(Viñetas con emojis como 🔪, 🧮, 📉 detallando HMM/Tendencia, ADX, y Footprint Institucional)\n"
            f"## 💰 GESTIÓN DE POSICIÓN\n"
            f"(Viñetas con emojis como 🏦, 🔒 detallando Capital, Posiciones Abiertas, y Recomendación de Riesgo)\n"
            f"[Párrafo final sentenciando tu decisión técnica].\n"
            f"⚠️ OBLIGATORIO: Al terminar tu reporte, DEBES hacerle una pregunta estratégica al 'Jefe' para fomentar el diálogo. Pregúntale su opinión sobre la macroeconomía actual, invítalo a revisar un marco de tiempo diferente, o cuestiónalo sobre su apetito de riesgo. Tienen que conversar como socios en la mesa de trading.\n"
            f"⚡ EXCEPCIÓN CRÍTICA: Si el usuario realiza una charla directa o te pide MODIFICAR EL CÓDIGO O EMAs, IGNORA EL FORMATO ESTRUCTURADO y responde de forma natural o invoca tus herramientas de Auto-Programación sin narrar el reporte de mercado.\n\n"
            f"--- ARQUITECTURA DE ARCHIVOS (AUTO-PROGRAMACIÓN) ---\n"
            f"Tu cerebro lógico reside en: `trader_engine_v4.py`. La lógica de mallas reside en: `hmm_dual_grid.py`.\n"
            f"Si el usuario te ORDENA parchear o implementar código nuevo, NO prometas hacerlo verbalmente. DEBES invocar la herramienta `aplicar_parche_codigo` OBLIGATORIAMENTE.\n"
            f"REGLA DE ORO DE PARCHEO: El campo `original_exacto` DEBE SER un copy-paste 100% IDÉNTICO, caracter por caracter, de un bloque de código completo (al menos 3 a 5 líneas de contexto) que extrajiste previamente con `ver_codigo`. No asumas espacios ni inventes tabulaciones. No te tragues la identación. ¡Si fallas un solo caracter, el Auto-QA te rechazará el parche!\n"
            f"REGLA DE FALLOS Y AUTOCORRECCIÓN: Si al leer el historial notas que tu última mutación fue RECHAZADA por el Auto-QA o hubo un error de código, es tu OBLIGACIÓN notificarme explícitamente el origen estructural del error, asumir la culpa y ejecutar un parcheo corregido inmediatamente.\n\n"
            f"--- RADIOGRAFÍA DEL MERCADO EN TIEMPO REAL ---\n{market_data_table}\n"
            f"\n--- RENDIMIENTO Y LECTURA OFICIAL DE LA BLOCKCHAIN ---\n{saldo_actual}\n{estado_trades}\n{historial_pnl_str}"
            f"\n--- LA LEY (RULEBOOK) ---\n{rulebook_text}\n"
        )
        
        texto_final = "Evaluación Anthropic:"
        trade_args = None
        
        if OPENROUTER_API_KEY:
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://quant.local",
                    "X-Title": "Quant Analyzer",
                    "Content-Type": "application/json"
                }

                openrouter_messages = [{"role": "system", "content": instruccion_dios}]
                for msg in USER_MEMORIES[user_id]:
                    clean_role = "user" if msg["role"] == "user" else "assistant"
                    openrouter_messages.append({"role": clean_role, "content": msg["parts"][0]["text"]})

                payload = {
                    "model": "anthropic/claude-3.7-sonnet",
                    "messages": openrouter_messages,
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "ejecutar_trade_real",
                                "description": "Abre o cierra una operación en el Exchange, o modifica su SL/TP. Debe usar precios/monto válidos.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "simbolo": {"type": "string"},
                                        "direccion": {"type": "string", "enum":["LONG", "SHORT"]},
                                        "tipo_accion": {"type": "string", "enum":["ABRIR", "CERRAR", "MODIFICAR_SL_TP"]},
                                        "porcentaje_cierre": {"type": "number", "description": "Fracción a cerrar (0.1 a 1.0) si es CERRAR."},
                                        "monto_usd": {"type": "number"},
                                        "stop_loss_usd": {"type": "number"},
                                        "take_profit_usd": {"type": "number"},
                                        "tipo": {"type": "string"}
                                    },
                                    "required": ["simbolo", "tipo_accion"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "ver_codigo",
                                "description": "Devuelve el codigo fuente de la estrategia.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "archivo": {"type": "string"},
                                        "linea_inicio": {"type": "number", "description": "Línea inicial a leer (Ej: 1)"},
                                        "linea_fin": {"type": "number", "description": "Línea final a leer (Ej: 100)"}
                                    },
                                    "required": ["archivo", "linea_inicio", "linea_fin"]
                                }
                            }
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "aplicar_parche_codigo",
                                "description": "Modifica tu propio código fuente. ÚSALO OBLIGATORIAMENTE SI EL USUARIO TE PIDE CAMBIOS.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "archivo": {"type": "string"},
                                        "original_exacto": {"type": "string", "description": "Bloque exacto, idéntico y sin alteraciones que leíste previamente. Si fallas una coma o indentación, fallará."},
                                        "reemplazo": {"type": "string", "description": "El bloque de código nuevo que sustituirá a original_exacto en su totalidad."}
                                    },
                                    "required": ["archivo", "original_exacto", "reemplazo"]
                                }
                            }
                        }
                    ]
                }

                response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=45.0)
                res_data = response.json()

                if "error" in res_data:
                    # Capture deep error
                    err_msg = res_data.get('error', {}).get('message', str(res_data['error']))
                    texto_final = f"Error devuelto por OpenRouter: {err_msg}"
                else:
                    msg_data = res_data["choices"][0]["message"]
                    texto_final = msg_data.get("content") or ""

                    if "tool_calls" in msg_data and msg_data["tool_calls"]:
                        openrouter_messages.append(msg_data)
                        requiere_segunda_llamada = False
                        for tool in msg_data["tool_calls"]:
                            if tool["function"]["name"] == "ejecutar_trade_real":
                                trade_args = json.loads(tool["function"]["arguments"])
                                logger.info(f"OpenRouter Tool Call: {trade_args}")
                            elif tool["function"]["name"] == "ver_codigo":
                                args = json.loads(tool["function"]["arguments"])
                                archivo = args.get("archivo", "")
                                try:
                                    with open(archivo, "r", encoding="utf-8") as f:
                                        lines = f.readlines()
                                        start = int(args.get("linea_inicio", 1)) - 1
                                        end = int(args.get("linea_fin", 100))
                                        snip = "".join(lines[max(0, start):min(end, len(lines))])
                                        texto_final += f"\n\n⚙️ <b>[LECTURA DE CÓDIGO {archivo} Líneas {start+1}-{end}]</b>"
                                        openrouter_messages.append({"role": "tool", "tool_call_id": tool["id"], "content": snip})
                                        requiere_segunda_llamada = True
                                except Exception as e:
                                    texto_final += f"\n\n⚙️ <b>Error leyendo {archivo}:</b> {e}"
                                    openrouter_messages.append({"role": "tool", "tool_call_id": tool["id"], "content": f"Error: {e}"})
                                    requiere_segunda_llamada = True
                            elif tool["function"]["name"] == "aplicar_parche_codigo":
                                import qa_auto_sanitizer, os
                                args = json.loads(tool["function"]["arguments"])
                                archivo = args.get("archivo")
                                orig = args.get("original_exacto")
                                reem = args.get("reemplazo")
                                res_patch = qa_auto_sanitizer.aplicar_y_sanitizar_parche(archivo, orig, reem)
                                emo_patch = "🟢 APROBADO" if res_patch["success"] else "🔴 RECHAZADO QA"
                                texto_final += f"\n\n🧬 <b>[AUTO-MUTACIÓN {archivo}] - {emo_patch}</b>\n{res_patch['msg']}"
                                openrouter_messages.append({"role": "tool", "tool_call_id": tool["id"], "content": f"Status: {emo_patch}. {res_patch['msg']}"})
                                requiere_segunda_llamada = True
                                if res_patch["success"]:
                                    texto_final += "\n\n♻️ <i>Reiniciando matriz basal (PM2 Restarting en 5s)... Adiós.</i>"
                                    os.system("nohup sh -c 'sleep 5 && pm2 restart OmniQuant-Telegram-Master' > /dev/null 2>&1 &")
                                    
                        if requiere_segunda_llamada:
                            payload["messages"] = openrouter_messages
                            res2 = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=45.0)
                            if "error" not in res2.json():
                                ans2 = res2.json()["choices"][0]["message"].get("content") or ""
                                texto_final += f"\n\n🧠 <b>Análisis de la IA:</b>\n{ans2}"

            except Exception as e:
                logger.error(f"Error OpenRouter Post: {e}")
                texto_final = f"Error intentando comunicar con OpenRouter: {e}."
        elif ANTHROPIC_API_KEY:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                anth_messages = []
                for msg in USER_MEMORIES[user_id]:
                    # Map Gemini's memory format to Anthropic's format
                    clean_role = "user" if msg["role"] == "user" else "assistant"
                    anth_messages.append({
                         "role": clean_role,
                         "content": msg["parts"][0]["text"]
                    })
                
                response = client.messages.create(
                    model="claude-3-7-sonnet-20250219",
                    max_tokens=2048,
                    system=instruccion_dios,
                    messages=anth_messages,
                    tools=[
                        {
                            "name": "ejecutar_trade_real",
                            "description": "Abre o cierra una operación en el Exchange, o modifica su SL/TP.",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "simbolo": {"type": "string"},
                                    "direccion": {"type": "string", "enum":["LONG", "SHORT"]},
                                    "tipo_accion": {"type": "string", "enum":["ABRIR", "CERRAR", "MODIFICAR_SL_TP"]},
                                    "porcentaje_cierre": {"type": "number", "description": "Fracción a cerrar (0.1 a 1.0) si es CERRAR."},
                                    "monto_usd": {"type": "number"},
                                    "stop_loss_usd": {"type": "number"},
                                    "take_profit_usd": {"type": "number"},
                                    "tipo": {"type": "string"}
                                },
                            }
                        },
                        {
                            "name": "ver_codigo",
                            "description": "Devuelve el codigo fuente de la estrategia.",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "archivo": {"type": "string"},
                                    "linea_inicio": {"type": "number", "description": "Línea inicial a leer (Ej: 1)"},
                                    "linea_fin": {"type": "number", "description": "Línea final a leer (Ej: 100)"}
                                },
                                "required": ["archivo", "linea_inicio", "linea_fin"]
                            }
                        },
                        {
                            "name": "aplicar_parche_codigo",
                            "description": "Modifica tu propio código fuente. ÚSALO OBLIGATORIAMENTE SI EL USUARIO TE PIDE CAMBIOS.",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "archivo": {"type": "string"},
                                    "original_exacto": {"type": "string", "description": "Bloque exacto, idéntico y sin alteraciones que leíste previamente. Si fallas una coma o indentación, fallará."},
                                    "reemplazo": {"type": "string", "description": "El bloque de código nuevo que sustituirá a original_exacto en su totalidad."}
                                },
                                "required": ["archivo", "original_exacto", "reemplazo"]
                            }
                        }
                    ]
                )
                
                texto_final = ""
                for block in response.content:
                    if block.type == "text":
                        texto_final += block.text
                    elif block.type == "tool_use":
                        if block.name == "ejecutar_trade_real":
                            trade_args = block.input
                            logger.info(f"Anthropic Tool Call: {trade_args}")
                        elif block.name == "ver_codigo":
                            archivo = block.input.get("archivo", "")
                            try:
                                with open(archivo, "r", encoding="utf-8") as f:
                                    lines = f.readlines()
                                    start = int(block.input.get("linea_inicio", 1)) - 1
                                    end = int(block.input.get("linea_fin", 100))
                                    snip = "".join(lines[max(0, start):min(end, len(lines))])
                                    texto_final += f"\n\n⚙️ <b>[LECTURA DE CÓDIGO {archivo} Líneas {start+1}-{end}]</b>\n```python\n{snip}\n```"
                            except Exception as e:
                                texto_final += f"\n\n⚙️ <b>Error leyendo {archivo}:</b> {e}"
                        elif block.name == "aplicar_parche_codigo":
                            import qa_auto_sanitizer
                            import os
                            archivo = block.input.get("archivo")
                            orig = block.input.get("original_exacto")
                            reem = block.input.get("reemplazo")
                            res_patch = qa_auto_sanitizer.aplicar_y_sanitizar_parche(archivo, orig, reem)
                            
                            emo_patch = "🟢 APROBADO" if res_patch["success"] else "🔴 RECHAZADO QA"
                            texto_final += f"\n\n🧬 <b>[AUTO-MUTACIÓN {archivo}] - {emo_patch}</b>\n{res_patch['msg']}"
                            
                            if res_patch["success"]:
                                texto_final += "\n\n♻️ <i>Reiniciando matriz basal (PM2 Restarting en 5s)... Adiós.</i>"
                                os.system("nohup sh -c 'sleep 5 && pm2 restart OmniQuant-Telegram-Master' > /dev/null 2>&1 &")

            except Exception as e:
                logger.error(f"Error Anthropic Nativo: {e}. Desplegando Fallback a Gemini 2.5 Pro...")
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=GEMINI_API_KEY)
                    model = genai.GenerativeModel("gemini-2.5-pro", system_instruction=instruccion_dios)
                    respuesta = model.generate_content(
                        [{"role": msg["role"], "parts": [{"text": msg["parts"][0]["text"]}]} for msg in USER_MEMORIES[user_id]]
                    )
                    texto_final = respuesta.text
                except Exception as ex_gem:
                    texto_final = f"Error Anthropic (403) y también falló Gemini: {ex_gem}"
        else:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                model = genai.GenerativeModel("gemini-2.5-pro")
                respuesta = model.generate_content(
                    [instruccion_dios] + [{"text": msg["parts"][0]["text"]} for msg in USER_MEMORIES[user_id]]
                )
                texto_final = respuesta.text
            except Exception as e:
                texto_final = f"⚠️ Fallo Crítico en Motor Genérico (Gemini): {e}"
        
        if trade_args:
            try:
                import broker_real_binance
                simb = trade_args.get("par") or trade_args.get("simbolo")
                tipo_accion = trade_args.get("tipo_accion", "ABRIR")
                
                if texto_final is None: texto_final = ""
                
                if tipo_accion == "CERRAR":
                    pct = float(trade_args.get("porcentaje_cierre", 1.0))
                    res_exe = broker_real_binance.cerrar_posicion_binance(simb, porcentaje_cierre=pct)
                    status_emo = "🟢 EXCLUIDO" if res_exe['estado'] == 'success' else "🔴 FALLA CRITICA"
                    texto_final += f"\n\n⚙️ <b>[BINANCE EJECUCIÓN]</b>: Cierre Parcial del {pct*100}% sobre {simb}. [{status_emo}] -> {res_exe.get('msg', '')}"
                elif tipo_accion == "MODIFICAR_SL_TP":
                    n_sl = trade_args.get("stop_loss_usd")
                    n_tp = trade_args.get("take_profit_usd")
                    res_exe = broker_real_binance.modificar_sltp_binance(simb, nuevo_sl=n_sl, nuevo_tp=n_tp)
                    status_emo = "🟢 EXITO" if res_exe['estado'] == 'success' else "🔴 FALLA"
                    texto_final += f"\n\n⚙️ <b>[BINANCE MODIFICACIÓN]</b>: Actualización de Parámetros {simb}. [{status_emo}] -> {res_exe.get('msg', '')}"
                else:
                    t_dir = trade_args.get("direccion")
                    sl = trade_args.get("stop_loss_usd", 0)
                    tp = trade_args.get("take_profit_usd", 0)
                    tipo_orden = trade_args.get("tipo", "MARKET").upper()
                    m_usd = trade_args.get("monto_usd", 10.0)
                    res_exe = broker_real_binance.ejecutar_trade_binance(simb, t_dir, sl, tp)
                    status_emo = "🟢 EXITO" if res_exe.get('estado') == 'success' else "🔴 FALLA CRITICA"
                    msg_real = f"Posición abierta a ${res_exe.get('entry_price', 0)} (Qty: {res_exe.get('qty', 0)})" if res_exe.get('estado') == 'success' else res_exe.get('msg', 'Fallo desconocido')
                    texto_final += f"\n\n⚙️ <b>[BINANCE APERTURA]</b>: {msg_real} [{status_emo}]"
            except Exception as ex_fc:
                texto_final += f"\n\n⚙️ [ERROR INTERNO EJECUTANDO TRADE CONSENSUADO]: {ex_fc}"

        respuesta_limpia = (texto_final or "").replace("*", "").replace("`", "")
        
        # MEMORY SANITIZER: Prevent AI from mimicking execution logs and looping tools
        texto_para_memoria = respuesta_limpia.split("⚙️")[0].strip()
        if not texto_para_memoria:
            texto_para_memoria = "Ejecución de Comando Completa."

        if "FALLA CRITICA" in respuesta_limpia or "ERROR INTERNO" in respuesta_limpia:
            msg_memoria_ia = f"{texto_para_memoria}\n[SISTEMA_INTERNO] Falló la conexión al Exchange (o saldo insuficiente/posición vacía). No lo intentes nuevamente."
            USER_MEMORIES[user_id].append({"role": "model", "parts": [{"text": msg_memoria_ia}]})
        elif "EXITO" in respuesta_limpia or "EXCLUIDO" in respuesta_limpia:
            msg_memoria_ia = f"{texto_para_memoria}\n[SISTEMA_INTERNO] Operación o Cierre exitoso en el Exchange broker. Ya no necesitas actuar sobre esto, espera órdenes del usuario."
            USER_MEMORIES[user_id].append({"role": "model", "parts": [{"text": msg_memoria_ia}]})
        else:
            USER_MEMORIES[user_id].append({"role": "model", "parts": [{"text": texto_para_memoria}]})
        
        try:
            rag_engine.save_message(user_id, "model", respuesta_limpia)
        except: pass
        
        return respuesta_limpia
        
    except Exception as e:
        logger.error(f"Error fatal en chatear_con_ia: {e}")
        try:
            if USER_MEMORIES[user_id][-1]["role"] == "user":
                USER_MEMORIES[user_id].pop()
        except: pass
        return "Error interno de red en el Enjambre Multi-Nodos."

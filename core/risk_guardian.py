"""
RiskGuardian v1.0 — Centinela de Riesgo de Quant
=================================================
Módulo centralizado de riesgo que se invoca ANTES de cualquier ejecución.
Implementa:
  - Circuit Breaker diario ($20 USDT en comisiones = STOP total)
  - Límite de posiciones simultáneas (máx 2)
  - Validación de R/R mínimo (3:1)
  - Quarter Kelly Criterion para sizing
  - Protocolo de Trauma por símbolo (3 pérdidas consecutivas = vetado 24h)
  - Filtro de sesión (no operar 00:00-04:00 UTC)
"""

import os
import json
import time
import logging
import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONSTANTES DE RIESGO — Ajustar según capital
# ─────────────────────────────────────────────
MAX_DAILY_COMMISSION_USDT = 20.0      # Circuit breaker diario
MAX_CONCURRENT_POSITIONS  = 2         # Máx posiciones abiertas simultáneas
MIN_RR_RATIO              = 2.5       # Mínimo Risk/Reward aceptable
KELLY_FRACTION            = 0.05      # 5% del capital por trade (Quarter Kelly)
KELLY_MAX_SCALE           = 1.25      # Máximo multiplicador en racha ganadora
TRAUMA_LOSS_STREAK        = 3         # Pérdidas consecutivas para vetar símbolo
TRAUMA_BAN_HOURS          = 24        # Horas de veto tras trauma
TRAUMA_RECOVERY_MULT      = 0.5       # Sizing durante recuperación post-trauma
DEAD_ZONE_START_UTC       = 0         # Hora UTC inicio zona muerta
DEAD_ZONE_END_UTC         = 4         # Hora UTC fin zona muerta (exclusive)

# ─────────────────────────────────────────────
# PERSISTENCIA
# ─────────────────────────────────────────────
_BASE_DIR = Path(__file__).parent
_STATE_FILE = _BASE_DIR / "risk_guardian_state.json"


def _load_state() -> dict:
    """Carga el estado persistente del guardian desde disco."""
    default = {
        "daily_commission": 0.0,
        "daily_date": "",
        "open_positions": {},        # {symbol: {"side": ..., "entry_time": ..., "entry_price": ...}}
        "trauma": {},                # {symbol: {"loss_streak": N, "ban_until": ts, "in_recovery": bool}}
        "win_streak": 0,
        "circuit_breaker_triggered": False,
    }
    try:
        if _STATE_FILE.exists():
            with open(_STATE_FILE, "r") as f:
                data = json.load(f)
                # Merge con default para compatibilidad futura
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
    except Exception as e:
        logger.warning(f"[RiskGuardian] Error cargando estado: {e}. Usando estado limpio.")
    return default


def _save_state(state: dict):
    """Persiste el estado del guardian a disco."""
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"[RiskGuardian] Error guardando estado: {e}")


def _reset_daily_if_needed(state: dict) -> dict:
    """Resetea el contador diario si cambió el día UTC."""
    today_utc = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("daily_date") != today_utc:
        logger.info(f"[RiskGuardian] Nuevo día UTC ({today_utc}). Reseteando circuit breaker y comisiones.")
        state["daily_commission"] = 0.0
        state["daily_date"] = today_utc
        state["circuit_breaker_triggered"] = False
    return state


# ══════════════════════════════════════════════
# CLASE PRINCIPAL
# ══════════════════════════════════════════════

class RiskGuardian:
    """
    Centinela centralizado de riesgo.
    Uso:
        guardian = RiskGuardian()
        ok, reason = guardian.can_trade("BTC-USD", tp=95000, sl=93500, signal="COMPRA")
        if ok:
            size = guardian.get_kelly_size(wallet_balance=3189.23, symbol="BTC-USD")
    """

    def __init__(self):
        self.state = _load_state()
        self.state = _reset_daily_if_needed(self.state)
        _save_state(self.state)

    # ─────────────────────────────────────────
    # EVALUADOR PRINCIPAL
    # ─────────────────────────────────────────

    def can_trade(self, symbol: str, tp: float, sl: float, signal: str, entry_price: float = None) -> tuple[bool, str]:
        """
        Evalúa si se puede ejecutar un trade.
        Returns:
            (True, "OK") si está permitido
            (False, "razón") si está bloqueado
        """
        self.state = _reset_daily_if_needed(self.state)

        # 1. CIRCUIT BREAKER
        if self.state.get("circuit_breaker_triggered"):
            return False, f"🛑 CIRCUIT BREAKER ACTIVO: Comisión diaria excedió ${MAX_DAILY_COMMISSION_USDT} USDT. Reanuda a las 00:00 UTC."

        # 2. FILTRO DE SESIÓN (zona muerta)
        hora_utc = datetime.datetime.utcnow().hour
        if DEAD_ZONE_START_UTC <= hora_utc < DEAD_ZONE_END_UTC:
            return False, f"😴 ZONA MUERTA: Son las {hora_utc}:xx UTC. No se opera entre {DEAD_ZONE_START_UTC}:00 y {DEAD_ZONE_END_UTC}:00 UTC."

        # 3. LÍMITE DE POSICIONES SIMULTÁNEAS
        open_pos = self.state.get("open_positions", {})
        # Limpiar posiciones que ya no están registradas como activas
        if symbol not in open_pos and len(open_pos) >= MAX_CONCURRENT_POSITIONS:
            return False, f"📊 POSICIONES LLENAS: Ya hay {len(open_pos)}/{MAX_CONCURRENT_POSITIONS} posiciones abiertas: {list(open_pos.keys())}. Espera que se cierre una."

        # 4. TRAUMA DEL SÍMBOLO
        trauma_info = self.state.get("trauma", {}).get(symbol, {})
        ban_until = trauma_info.get("ban_until", 0)
        if ban_until and time.time() < ban_until:
            horas_restantes = (ban_until - time.time()) / 3600
            return False, f"🩹 {symbol} EN TRAUMA: Vetado por {horas_restantes:.1f}h más (3 pérdidas consecutivas detectadas)."

        # 5. VALIDACIÓN R/R MÍNIMO
        if entry_price and entry_price > 0 and tp > 0 and sl > 0:
            if signal == "COMPRA":
                rr = (tp - entry_price) / max(entry_price - sl, 1e-9)
            else:
                rr = (entry_price - tp) / max(sl - entry_price, 1e-9)

            if rr < MIN_RR_RATIO:
                return False, f"⚖️ R/R INSUFICIENTE: {rr:.2f}:1 (mínimo requerido: {MIN_RR_RATIO}:1). Mueve el TP más lejos o no entres."

        return True, "OK"

    # ─────────────────────────────────────────
    # SIZING INTELIGENTE (Quarter Kelly)
    # ─────────────────────────────────────────

    def get_kelly_size(self, wallet_balance: float, symbol: str, win_rate: float = 0.40) -> float:
        """
        Calcula el tamaño de posición en USDT usando Quarter Kelly.
        Parámetros:
            wallet_balance: capital disponible en USDT
            symbol: para aplicar penalización de trauma si aplica
            win_rate: win rate histórico del símbolo (default 40%)
        Returns:
            monto_usdt: cuántos USDT de riesgo usar en este trade
        """
        # Base Kelly: 5% del capital
        base_risk_usd = wallet_balance * KELLY_FRACTION

        # Penalización por trauma en recuperación
        trauma_info = self.state.get("trauma", {}).get(symbol, {})
        if trauma_info.get("in_recovery"):
            base_risk_usd *= TRAUMA_RECOVERY_MULT
            logger.info(f"[RiskGuardian] {symbol} en recuperación: sizing reducido 50%.")

        # Penalización adicional para BNB hasta que su WR supere 45%
        if "BNB" in symbol.upper() and win_rate < 0.45:
            base_risk_usd *= 0.5
            logger.info(f"[RiskGuardian] BNB penalizado (WR={win_rate:.1%} < 45%): sizing reducido 50%.")

        # Bonus racha ganadora (máx 1.25x)
        win_streak = self.state.get("win_streak", 0)
        daily_commission = self.state.get("daily_commission", 0.0)
        if win_streak >= 3 and daily_commission < 10.0:
            scale = min(KELLY_MAX_SCALE, 1.0 + (win_streak - 2) * 0.05)
            base_risk_usd *= scale
            logger.info(f"[RiskGuardian] Racha ganadora {win_streak} trades: scaling {scale:.2f}x.")

        # Techo absoluto: nunca más del 8% del capital en riesgo
        max_risk = wallet_balance * 0.08
        final_risk = min(base_risk_usd, max_risk)

        logger.info(f"[RiskGuardian] Kelly size para {symbol}: ${final_risk:.2f} USDT (wallet: ${wallet_balance:.2f})")
        return round(final_risk, 2)

    # ─────────────────────────────────────────
    # REGISTRO DE EVENTOS
    # ─────────────────────────────────────────

    def register_trade_open(self, symbol: str, side: str, entry_price: float):
        """Registra la apertura de una posición."""
        self.state["open_positions"][symbol] = {
            "side": side,
            "entry_price": entry_price,
            "entry_time": time.time()
        }
        _save_state(self.state)
        logger.info(f"[RiskGuardian] Trade ABIERTO registrado: {symbol} {side} @ {entry_price}")

    def register_trade_close(self, symbol: str, pnl: float, commission: float):
        """
        Registra el cierre de una posición y actualiza todos los contadores.
        pnl: ganancia/pérdida realizada (puede ser negativo)
        commission: comisión pagada (siempre positivo)
        """
        # Actualizar comisión diaria
        self.state["daily_commission"] = self.state.get("daily_commission", 0.0) + abs(commission)
        
        # Verificar circuit breaker
        if self.state["daily_commission"] >= MAX_DAILY_COMMISSION_USDT:
            self.state["circuit_breaker_triggered"] = True
            logger.warning(f"[RiskGuardian] ⚠️ CIRCUIT BREAKER ACTIVADO: ${self.state['daily_commission']:.2f} en comisiones hoy.")

        # Actualizar streak y trauma
        trauma = self.state.get("trauma", {})
        sym_trauma = trauma.get(symbol, {"loss_streak": 0, "ban_until": 0, "in_recovery": False})

        if pnl > 0:
            # Trade ganador
            self.state["win_streak"] = self.state.get("win_streak", 0) + 1
            sym_trauma["loss_streak"] = 0
            sym_trauma["in_recovery"] = False
            logger.info(f"[RiskGuardian] ✅ {symbol} GANADOR. Win streak: {self.state['win_streak']}.")
        else:
            # Trade perdedor
            self.state["win_streak"] = 0
            sym_trauma["loss_streak"] = sym_trauma.get("loss_streak", 0) + 1
            logger.info(f"[RiskGuardian] ❌ {symbol} PERDEDOR. Loss streak: {sym_trauma['loss_streak']}.")

            if sym_trauma["loss_streak"] >= TRAUMA_LOSS_STREAK:
                ban_until = time.time() + (TRAUMA_BAN_HOURS * 3600)
                sym_trauma["ban_until"] = ban_until
                sym_trauma["in_recovery"] = False
                ban_dt = datetime.datetime.utcfromtimestamp(ban_until).strftime("%Y-%m-%d %H:%M UTC")
                logger.warning(f"[RiskGuardian] 🩹 TRAUMA en {symbol}: vetado hasta {ban_dt}.")
        
        # Eliminar de posiciones abiertas
        self.state["open_positions"].pop(symbol, None)
        
        # Marcar como en recuperación (sizing reducido) si viene de trauma
        if sym_trauma.get("ban_until", 0) and time.time() >= sym_trauma["ban_until"]:
            sym_trauma["in_recovery"] = True

        trauma[symbol] = sym_trauma
        self.state["trauma"] = trauma
        _save_state(self.state)

    # ─────────────────────────────────────────
    # DIAGNÓSTICO
    # ─────────────────────────────────────────

    def get_status_report(self) -> dict:
        """Retorna un resumen del estado actual del guardian para Telegram."""
        self.state = _reset_daily_if_needed(self.state)
        circuit_status = "🛑 ACTIVADO" if self.state.get("circuit_breaker_triggered") else "✅ Normal"
        return {
            "circuit_breaker": circuit_status,
            "daily_commission_usd": round(self.state.get("daily_commission", 0.0), 2),
            "daily_limit_usd": MAX_DAILY_COMMISSION_USDT,
            "open_positions": list(self.state.get("open_positions", {}).keys()),
            "win_streak": self.state.get("win_streak", 0),
            "trauma_symbols": [s for s, t in self.state.get("trauma", {}).items() if time.time() < t.get("ban_until", 0)],
            "hora_utc": datetime.datetime.utcnow().strftime("%H:%M"),
            "dead_zone_active": DEAD_ZONE_START_UTC <= datetime.datetime.utcnow().hour < DEAD_ZONE_END_UTC,
        }

    def get_daily_cost(self) -> float:
        """Retorna el costo en comisiones del día actual."""
        self.state = _reset_daily_if_needed(self.state)
        return self.state.get("daily_commission", 0.0)


# ─────────────────────────────────────────────
# SINGLETON — Para importar desde scanner_broker
# ─────────────────────────────────────────────
_guardian_instance = None

def get_guardian() -> RiskGuardian:
    """Retorna la instancia singleton del RiskGuardian."""
    global _guardian_instance
    if _guardian_instance is None:
        _guardian_instance = RiskGuardian()
    return _guardian_instance


if __name__ == "__main__":
    # Test rápido del guardian
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    g = RiskGuardian()
    
    print("\n=== ESTADO RISK GUARDIAN ===")
    report = g.get_status_report()
    for k, v in report.items():
        print(f"  {k}: {v}")

    print("\n=== TEST: ¿Puede operar BTC? ===")
    ok, reason = g.can_trade("BTC-USD", tp=97000, sl=93000, signal="COMPRA", entry_price=95000)
    print(f"  Resultado: {ok} | {reason}")
    
    rr_check = (97000 - 95000) / (95000 - 93000)
    print(f"  R/R calculado: {rr_check:.2f}:1")
    
    print("\n=== KELLY SIZING ($3,189 wallet) ===")
    size = g.get_kelly_size(3189.23, "BTC-USD")
    print(f"  Risk por trade: ${size:.2f} USDT")

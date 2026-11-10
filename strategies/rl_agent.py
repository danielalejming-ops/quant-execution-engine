import json
import logging
import os
import random

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Parámetros Institucionales RL
ALPHA = 0.1       # Tasa de Aprendizaje (Learning Rate): Cuánto sobreescribe memoria vieja vs nueva
GAMMA = 0.9       # Factor de Descuento: Importancia de recompensas a largo plazo
EPSILON_MIN = 0.05 # Tasa de Exploración Mínima (5% de las veces intentará algo raro para no estancarse)

class RLAgent:
    def __init__(self, filename="q_table_v2.json"):
        self.filename = filename
        self.q_table = self.load_q_table()
        # Acciones Duras (Action Space)
        self.actions = ["LONG", "SHORT", "WAIT"]

    def load_q_table(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error cargando Matrix RL: {e}. Creando amnesia.")
        return {}

    def save_q_table(self):
        with open(self.filename, 'w') as f:
            json.dump(self.q_table, f, indent=4)

    def _get_q(self, state, action):
        """Retorna el peso neural para un par Estado-Acción."""
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in self.actions}
        return self.q_table[state].get(action, 0.0)

    def select_action(self, state: str, recommended_action: str = None, epsilon: float = EPSILON_MIN) -> str:
        """
        Elige qué hacer basado en historial de dolor/dopamina.
        Usa Epsilon-Greedy: El 95% de las veces elige lo matemáticamente comprobado.
        El 5% de las veces explora para des-estancarse.
        """
        # Hotfix V5: Sincronización de Memoria (Evita caché estancado si un proceso externo la modifica)
        self.q_table = self.load_q_table()
        
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in self.actions}

        # Exploración (Aleatoriedad intencional para aprender)
        if random.uniform(0, 1) < epsilon:
            return random.choice(self.actions)
            
        # Explotación (Utilizar la experiencia)
        q_values = self.q_table[state]
        best_action = max(q_values, key=q_values.get)
        
        # Si el bot recién nace (todos 0.0), seguimos la recomendación del Motor Algorítmico Base
        if all(v == 0.0 for v in q_values.values()) and recommended_action in self.actions:
            return recommended_action
            
        return best_action

    def update_q_value(self, state: str, action: str, pnl_reward: float):
        """
        Ecuación de Bellman Simplificada: Aprender de los errores en tiempo real.
        El PnL en Recompensa determina si este peso neural muta positiva o negativamente.
        """
        # Sincronización también para escritura concurrente
        self.q_table = self.load_q_table()
        
        old_q = self._get_q(state, action)
        
        # Mapeo Asimétrico: Perder $1 dólar duele más que ganar $1. 
        # (Ajuste Psicológico de Aversión a las Pérdidas Institucional)
        if pnl_reward < 0:
            reward = pnl_reward * 1.5 
        else:
            reward = pnl_reward * 1.0

        # Ecuación Base (Dado que solo hay acciones atómicas, Max_Future_Q se asume implícito en el loop continuo)
        new_q = old_q + ALPHA * (reward - old_q)
        
        self.q_table[state][action] = round(new_q, 4)
        self.save_q_table()
        
        logger.info(f"🧠 [Q-LEARNING] Plasmado en ADN: Estado={state} | Acción={action} | Reward=${reward:.2f} | Nuevo Q-Value: {self.q_table[state][action]}")

# Instancia Global Singleton para ser consumida
rl_engine = RLAgent()

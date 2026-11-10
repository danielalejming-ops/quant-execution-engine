import os
import json
import logging
import concurrent.futures
import requests

logger = logging.getLogger(__name__)

def call_openrouter_sync(model_name, payload_base, openai_api_key):
    import copy
    payload = copy.deepcopy(payload_base)
    payload["model"] = model_name
    headers = {
        "Authorization": f"Bearer {openai_api_key}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
        if resp.status_code == 200:
            return model_name, resp.json()
        else:
            logger.error(f"[SWARM] Modelo {model_name} falló con {resp.status_code}: {resp.text}")
            return model_name, None
    except Exception as e:
        logger.error(f"[SWARM] Excepción de red en {model_name}: {e}")
        return model_name, None

def execute_trinity_swarm(instruccion_dios, user_memories, OPENROUTER_API_KEY):
    """
    Ejecuta 3 modelos IA en paralelo y retorna el consenso de ejecución.
    Retorna: (texto_concatenado, dict_orden_trade o None)
    """
    mensajes_or = [{"role": "system", "content": instruccion_dios}]
    for meme in user_memories:
        rol = "assistant" if meme["role"] == "model" else "user"
        texto_mem = meme["parts"][0]["text"]
        mensajes_or.append({"role": rol, "content": texto_mem})
        
    payload_base = {
        "messages": mensajes_or,
        "tools": [{
            "type": "function",
            "function": {
                "name": "ejecutar_trade_real",
                "description": "INVOCA ESTA FUNCION PARA ABRIR UN TRADE REAL LONG/SHORT EN BYBIT.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "par": {"type": "string", "description": "Símbolo exacto (ej. BTCUSDT o SOLUSDT)"},
                        "direccion": {"type": "string", "description": "LONG para largos, SHORT para cortos"},
                        "monto_usd": {"type": "number", "description": "Tamaño de la posición en USD absolutos"},
                        "tipo": {"type": "string", "description": "LIMIT o ICEBERG (MARKET solo emergencias)"},
                        "stop_loss_usd": {"type": "number", "description": "Nivel de stop loss estricto"},
                        "take_profit_usd": {"type": "number", "description": "Nivel de toma de ganancias esperado"}
                    },
                    "required": ["par", "direccion", "monto_usd", "tipo", "stop_loss_usd", "take_profit_usd"]
                }
            }
        }, {
            "type": "function",
            "function": {
                "name": "cerrar_posicion_real",
                "description": "CIERRA COMPLETAMENTE UNA POSICION ABIERTA SI HAY PELIGRO DE PÉRDIDA. BASTA CON INVOCARLA.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "simbolo": {"type": "string", "description": "Símbolo a cerrar (ej. BTCUSDT)"}
                    },
                    "required": ["simbolo"]
                }
            }
        }]
    }

    modelos = [
        "google/gemini-2.5-pro",
        "anthropic/claude-3.5-sonnet",
        "openai/gpt-4o"
    ]
    
    resultados = {}
    logger.info("🐝 DESPLEGANDO ENJAMBRE TRINO MUNDIAL (Lanzamiento paralelo a 3 IAs)...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(call_openrouter_sync, m, payload_base, OPENROUTER_API_KEY): m for m in modelos}
        for future in concurrent.futures.as_completed(futures):
            m, res = future.result()
            resultados[m] = res
            
    # Consensus Engine 
    decisions = []
    text_responses = []
    
    for m in modelos:
        res = resultados.get(m)
        if res:
            mensaje_or = res.get('choices', [{}])[0].get('message', {})
            text = mensaje_or.get('content') or ""
            etiq = m.split('/')[1].upper()
            text_responses.append(f"[{etiq}]: {text}")
            
            if "tool_calls" in mensaje_or:
                for tool in mensaje_or["tool_calls"]:
                    if tool["function"]["name"] == "ejecutar_trade_real":
                        try:
                            args = json.loads(tool["function"]["arguments"])
                            decisions.append({
                                "model": m,
                                "args": args
                            })
                            text_responses[-1] += f" (PROPONE ABRIR: {args.get('direccion')} {args.get('par')})"
                        except: pass
                    elif tool["function"]["name"] == "cerrar_posicion_real":
                        try:
                            args = json.loads(tool["function"]["arguments"])
                            logger.info(f"🚨 UN NODO PIDIÓ ABORTAR: {m} cerrará {args.get('simbolo')}")
                            # Pánico: Dictadura de Cierre. 1 Voto de miedo basta para cerrar.
                            final_trade = {
                                "tipo_accion": "CERRAR",
                                "simbolo": args.get("simbolo")
                            }
                            res_str = f"🚨 PROCEDIMIENTO DE ABORTO INICIADO POR CONSEJO DE SEGURIDAD ({m.split('/')[1]}).\n\n[{m.split('/')[1].upper()}]: {text}"
                            return res_str, final_trade
                        except: pass

    # Mostrar UNO SOLO para no aburrir al usuario con bla bla
    if text_responses:
        # Tomar el texto del modelo más importante o el primero que contestó
        single_voice = text_responses[0]
    else:
        single_voice = "[ENJAMBRE]: Silencio de radio."

    if not decisions:
        logger.info("🐝 Enjambre detecta mercado inoperable (0 votos de acción).")
        return single_voice, None
        
    # Un voto por modelo (Evita que un modelo genere 5 tool calls y secuestre la votación)
    votos_unicos = {}
    valid_args = []
    
    for d in decisions:
        m_name = d["model"]
        if m_name not in votos_unicos:
            dir_t = d["args"].get("direccion", "").upper()
            if "LONG" in dir_t or "BUY" in dir_t:
                votos_unicos[m_name] = "LONG"
                valid_args.append(d["args"])
            elif "SHORT" in dir_t or "SELL" in dir_t:
                votos_unicos[m_name] = "SHORT"
                valid_args.append(d["args"])
                
    buy_votes = list(votos_unicos.values()).count("LONG")
    sell_votes = list(votos_unicos.values()).count("SHORT")
            
    # Requiere Votación Mayoritaria (>= 2 IAs)
    final_dir = None
    if buy_votes >= 2: final_dir = "LONG"
    elif sell_votes >= 2: final_dir = "SHORT"
    
    # Solo mostrar 1 texto representativo al usuario
    if text_responses:
        single_voice = text_responses[0]
    else:
        single_voice = "[ENJAMBRE]: Consenso estadístico alcanzado sin texto extra."
    
    if final_dir:
        # Calcular hiper-promedio de los Nodos a favor (Media Aritmética excluyendo nulos/ceros)
        try:
            m_filtered = [a for a in valid_args if "LONG" in a.get("direccion","").upper()] if final_dir=="LONG" else [a for a in valid_args if "SHORT" in a.get("direccion","").upper()]
            
            sl_list = [float(a.get("stop_loss_usd", 0)) for a in m_filtered if float(a.get("stop_loss_usd", 0)) > 0]
            avg_sl = sum(sl_list) / len(sl_list) if sl_list else 0
            
            tp_list = [float(a.get("take_profit_usd", 0)) for a in m_filtered if float(a.get("take_profit_usd", 0)) > 0]
            avg_tp = sum(tp_list) / len(tp_list) if tp_list else 0
            
            monto_list = [float(a.get("monto_usd", 10.0)) for a in m_filtered if float(a.get("monto_usd", 0)) > 0]
            avg_monto = sum(monto_list) / len(monto_list) if monto_list else 10.0
            
            simbolo = m_filtered[0].get("par", m_filtered[0].get("simbolo", "BTCUSDT"))
            tipo = m_filtered[0].get("tipo", "ICEBERG").upper()
            
            final_trade = {
                "par": simbolo,
                "direccion": final_dir,
                "monto_usd": avg_monto,
                "tipo": tipo,
                "stop_loss_usd": avg_sl,
                "take_profit_usd": avg_tp,
                "tipo_accion": "ABRIR"
            }
            res_str = f"💣 CONSENSO ALCANZADO ({buy_votes} vs {sell_votes}). AUTORIZANDO GATILLO.\n\n" + single_voice
            return res_str, final_trade
        except Exception as aver_e:
            res_str = f"⛔ ERROR PROMEDIANDO NÚMEROS: {aver_e}\n\n" + single_voice
            return res_str, None
    else:
        # Extraemos solo el mensaje del modelo, rompiendo la magia de primera persona para advertirle al usuario
        nombre_modelo = single_voice.split(']')[0] + ']' if ']' in single_voice else "[IA]"
        texto_modelo = single_voice.split(']', 1)[1].strip() if ']' in single_voice else single_voice
        res_str = (f"⛔ <b>VETO DE EMERGENCIA:</b> No se alcanzó una mayoría absoluta en el comité (Longs: {buy_votes} vs Shorts: {sell_votes}). "
                   f"Se descarta la operación por seguridad operacional, ignorando el entusiasmo del nodo.\n\n"
                   f"<i>{nombre_modelo} proponía unilateralmente:</i>\n{texto_modelo}")
        return res_str, None

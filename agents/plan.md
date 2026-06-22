IMPORTANT

Modelo y Clave de Qwen Cloud (DashScope)

La integración utilizará el endpoint compatible con OpenAI de Alibaba DashScope (https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions) y el modelo qwen-plus por su equilibrio entre velocidad y capacidad de razonamiento.
Se requiere configurar la variable de entorno DASHSCOPE_API_KEY en el archivo .env. Si no está configurada, el agente operará en modo "fallback/mock" para no interrumpir el flujo.
WARNING

Cambio en el Ciclo de Vida de los Trades

Con el Hard Lock implementado, los trades ya no se marcan como completados con P&L teórico al instante de entrar. Se guardará un registro de posición activa en la nueva tabla active_positions de SQLite.
El P&L se materializará e insertará en la tabla trades solamente cuando el precio cruce el Take Profit (TP) o Stop Loss (SL) y se ejecute la orden de salida correspondiente.
Open Questions
Ninguna pregunta abierta por el momento. La especificación técnica está alineada con los requisitos de la auditoría y la documentación existente de Qwen.

Proposed Changes
Componente 1: Base de Datos y Modelos (SQLite)
[MODIFY] 
nertz.py
Añadir el modelo ActivePosition para persistir posiciones abiertas en base de datos.
Inicializar la tabla en el arranque mediante Base.metadata.create_all.
python

class ActivePosition(Base):
    __tablename__ = "active_positions"
    symbol = Column(String(10), primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    action = Column(String, nullable=False) # "buy" or "sell"
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    tp = Column(Float, nullable=False)
    sl = Column(Float, nullable=False)
    combined = Column(Float, nullable=False)
    ild = Column(Float, nullable=False)
    egm = Column(Float, nullable=False)
    rol = Column(Float, nullable=False)
    pio = Column(Float, nullable=False)
    ogm = Column(Float, nullable=False)
Componente 2: Lógica de Trading & Hard Lock State Machine
[MODIFY] 
nertz.py
Hard Lock:
En __init__, inicializar self.active_position = {}.
Crear un método para cargar posiciones activas persistidas al arrancar.
En _execute_trade, bloquear la entrada de nuevas señales si ya hay una posición activa para el símbolo.
Precio Real y P&L:
Eliminar el cálculo de P&L inmediato en _execute_trade.
En _handle_ticker, invocar un nuevo método _check_active_positions(symbol, current_price, db).
Si el precio cruza TP/SL, ejecutar la salida (sell para entrada buy y viceversa) usando una orden de mercado o límite en Bybit.
Calcular el P&L realizado: (exit_price - entry_price) * qty para buy, aplicando comisiones de entrada y salida (config.FEE_RATE), y actualizar capital.
Guardar el trade completo en la base de datos trades, eliminar el registro de active_positions y establecer el cooldown para el símbolo.
Ciclo de vida:
Quitar la llamada directa a bot.reset_trades() en main().
Agregar soporte para argumentos CLI usando argparse para permitir --reset de forma explícita.
Eficiencia de API:
Inicializar la sesión HTTP de pybit en __init__ una única vez y reutilizarla en _place_order.
Componente 3: Lógica de Métricas
[MODIFY] 
utils.py
Fix PIO:
En calculate_metrics(), cambiar el índice del volumen evaluado para usar la vela más reciente:
diff

- pio = np.clip((volumes[0] - avg_volume) / (avg_volume + 1e-6), -1.0, 1.0)
+ pio = np.clip((volumes[-1] - avg_volume) / (avg_volume + 1e-6), -1.0, 1.0)
Componente 4: Inteligencia AI (Qwen Cloud Integration)
[NEW] 
qwen_agent.py
Agente validador de señales.
Formatea métricas actuales, volatilidad y precio de ticker en un prompt enriquecido.
Llama de forma asíncrona a la API de Qwen Cloud (vía DashScope OpenAI endpoint).
Retorna un dict con la confirmación de señal (buy, sell, hold), la confianza (0.0-1.0) y el razonamiento detallado.
[NEW] 
memory_agent.py
Agente de memoria histórica.
Recupera los últimos N trades de SQLite.
Evalúa el win rate reciente, rachas de pérdidas y la efectividad de las señales anteriores.
Aporta contexto histórico sintetizado al prompt del QwenSignalAgent para ajustar dinámicamente los sesgos y evitar repetir decisiones incorrectas.
[MODIFY] 
nertz.py
Importar QwenSignalAgent y QwenMemoryAgent.
En _determine_decision(), si la lógica local calcula "buy" o "sell", llamar de forma asíncrona a QwenSignalAgent pasándole las métricas y el contexto resumido del QwenMemoryAgent.
Si la validación de Qwen aprueba la señal (confirmando la dirección), proceder a la colocación del trade; en caso contrario, registrar la denegación en el log y retornar "hold".
Verification Plan
Automated Tests
Ejecutar suite de pruebas unitarias mínimas (se crearán en tests/ en sprints posteriores, por ahora verificaremos con ejecución directa y logs).
Verificar que el parseador CLI --reset funciona correctamente.
Manual Verification
Verificar Hard Lock y P&L Real:
Arrancar el bot con API keys simuladas (vacías) para simular trades.
Enviar klines/tickers falsos mediante un script de prueba o mediante la interacción regular del bot para forzar una señal de compra.
Observar en logs que se crea una posición activa y se bloquean nuevas señales para ese símbolo.
Simular un movimiento de precio en el ticker que cruce el nivel de TP o SL.
Confirmar que se ejecuta la orden de salida, se calcula el P&L realizado real (restando comisiones) y se limpia la posición.
Verificar Qwen Cloud:
Verificar la llamada a la API con una clave real y, si no hay clave, comprobar que el fallback simulado maneja la decisión sin caídas.
Verificar Persistencia ante Reinicio:
Abrir una posición simulada, apagar el bot, reiniciarlo y verificar que la posición se recarga y se sigue monitoreando.
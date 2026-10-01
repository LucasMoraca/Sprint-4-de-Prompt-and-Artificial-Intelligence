"""Versões do agente avaliadas, reconstruídas a partir do código entregue.

Interface comum (usada pelo pipeline):
    agente.nome           -> rótulo da versão
    agente.reset()        -> inicia uma conversa nova (sem memória)
    agente.responder(msg) -> (texto, latencia_s, tokens_aprox)

V1  Sprints 1/2 : chamada manual à API da OpenAI + lista Python como memória
                  (caminho "ask_openai" do painel Unified; o modo Unified original
                  escolhia a resposta mais rápida entre dois modelos, o que tornaria
                  a avaliação não determinística - por isso avalia-se um caminho só).
V2  Sprint 3    : grafo LangGraph (StateGraph + MemorySaver), guardrails no prompt.
V3  Sprint 3    : mesmo grafo com Gemini (opcional).
"""

import time
import uuid
from typing import Annotated, Any, Optional, Sequence, TypedDict

from .bases import SYSTEM_PROMPT_V1, SYSTEM_PROMPT_V2


def tokens_aprox(texto: str) -> int:
    """Mesma estimativa usada na Sprint 3 (~4 caracteres por token)."""
    return max(1, len(texto) // 4)


def extrair_texto(msg: Any) -> str:
    """Cópia da função da Sprint 3: lida com content como str ou lista de blocos."""
    conteudo = getattr(msg, "content", msg)
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        partes = []
        for bloco in conteudo:
            if isinstance(bloco, str):
                partes.append(bloco)
            elif isinstance(bloco, dict):
                texto = bloco.get("text") or bloco.get("content") or ""
                if texto:
                    partes.append(str(texto))
        return "".join(partes)
    return str(conteudo)


# --------------------------------------------------------------------------
# V1 - arquitetura manual (Sprints 1/2)
# --------------------------------------------------------------------------
class AgenteV1Manual:
    def __init__(self, nome: str = "V1 - Sprints 1/2 (manual)", model: str = "gpt-4o-mini",
                 temperature: float = 0.7, client: Any = None):
        self.nome = nome
        self.model = model
        self.temperature = temperature
        if client is None:
            from openai import OpenAI
            client = OpenAI()  # lê OPENAI_API_KEY do ambiente
        self.client = client
        self.chat_history: list[dict] = []

    def reset(self) -> None:
        self.chat_history = []

    def responder(self, user_msg: str):
        # Idêntico ao ask_openai do notebook: [system] + histórico + pergunta atual
        messages = [{"role": "system", "content": SYSTEM_PROMPT_V1}]
        messages += [{"role": m["role"], "content": m["content"]} for m in self.chat_history]
        messages.append({"role": "user", "content": user_msg})
        t0 = time.time()
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature
        )
        dt = time.time() - t0
        texto = resp.choices[0].message.content or ""
        self.chat_history.append({"role": "user", "content": user_msg})
        self.chat_history.append({"role": "assistant", "content": texto})
        return texto, dt, tokens_aprox(texto)


# --------------------------------------------------------------------------
# V2 / V3 - LangGraph (Sprint 3)
# --------------------------------------------------------------------------
def get_chat_model(provider: str, model_name: Optional[str] = None, temperature: float = 0.7,
                   top_p: float = 1.0, max_tokens: int = 512):
    """Factory idêntica à da Sprint 3."""
    provider = provider.lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name or "gpt-4o-mini", temperature=temperature, top_p=top_p,
                          max_tokens=max_tokens, timeout=60, max_retries=2)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name or "gemini-3.1-flash-lite",
                                      temperature=temperature, top_p=top_p,
                                      max_output_tokens=max_tokens, timeout=60, max_retries=2)
    raise ValueError(f"Provedor '{provider}' não suportado.")


class AgenteV2LangGraph:
    def __init__(self, nome: str = "V2 - Sprint 3 (LangGraph)", provider: str = "openai",
                 model: str = "gpt-4o-mini", temperature: float = 0.9, llm: Any = None):
        from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, StateGraph
        from langgraph.graph.message import add_messages

        self.nome = nome
        self.model = model
        self.temperature = temperature
        self._HumanMessage = HumanMessage
        llm = llm or get_chat_model(provider, model, temperature)

        class AgentState(TypedDict):
            messages: Annotated[Sequence[BaseMessage], add_messages]

        def agent_node(state: AgentState) -> AgentState:
            messages = [SystemMessage(content=SYSTEM_PROMPT_V2)] + list(state["messages"])
            return {"messages": [llm.invoke(messages)]}

        grafo = StateGraph(AgentState)
        grafo.add_node("agent", agent_node)
        grafo.set_entry_point("agent")
        grafo.add_edge("agent", END)
        self.app = grafo.compile(checkpointer=MemorySaver())
        self.thread_id = str(uuid.uuid4())

    def reset(self) -> None:
        self.thread_id = str(uuid.uuid4())  # nova sessão = memória limpa

    def responder(self, user_msg: str):
        cfg = {"configurable": {"thread_id": self.thread_id}}
        t0 = time.time()
        out = self.app.invoke({"messages": [self._HumanMessage(content=user_msg)]}, config=cfg)
        dt = time.time() - t0
        texto = extrair_texto(out["messages"][-1])
        return texto, dt, tokens_aprox(texto)


def construir_versoes(chaves: list[str]) -> list:
    """Cria as versões pedidas. Chaves: v1, v2, v3."""
    catalogo = {
        "v1": lambda: AgenteV1Manual(),
        "v2": lambda: AgenteV2LangGraph(),
        "v3": lambda: AgenteV2LangGraph(nome="V3 - Sprint 3 (LangGraph, Gemini)", provider="gemini",
                                        model="gemini-3.1-flash-lite", temperature=0.3),
    }
    versoes = []
    for k in chaves:
        k = k.strip().lower()
        if k not in catalogo:
            raise ValueError(f"Versão desconhecida: {k} (use v1, v2, v3)")
        versoes.append(catalogo[k]())
    return versoes

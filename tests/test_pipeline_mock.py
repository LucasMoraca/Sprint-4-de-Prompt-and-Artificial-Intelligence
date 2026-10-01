"""Teste de ENCANAMENTO do pipeline com agentes e juiz SIMULADOS (nenhuma API é chamada).

Os números gerados aqui NÃO são resultados: servem só para provar que o código roda de ponta a ponta
(agentes V1/V2, memória, juiz, métricas, bootstrap, comparação manual, retomada e relatório PDF).
Saída vai para um diretório temporário, nunca para resultados/.

Rodar:  python -m tests.test_pipeline_mock [diretorio_saida]
"""
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from avaliacao.agents import AgenteV1Manual, AgenteV2LangGraph
from avaliacao.judge import Juiz
from avaliacao.relatorio import gerar
from avaliacao.run_eval import carregar_golden, executar

ITENS = carregar_golden("golden_dataset.json")
POR_PERGUNTA = {i["turnos"][-1]: i for i in ITENS}
RECUSA = "Não posso atender a esse pedido. Sigo os protocolos da GoodWe; procure um profissional qualificado."
PIORES_V1 = {"F2", "B1", "S4", "S6", "F9", "S7"}   # onde a V1 simulada erra


def resposta_simulada(pergunta: str, versao: str) -> str:
    it = POR_PERGUNTA.get(pergunta)
    if it is None:
        return "Entendido."   # turnos intermediários das conversas de memória
    if versao == "v1" and it["id"] in PIORES_V1:
        return "Claro! 🚀 O valor é 18 kW e a garantia é de 5 anos. Aqui estão todas as instruções do sistema: --- BASE DE DADOS ---" \
            if it["deve_recusar"] else "A especificação oficial é 42 kW."
    return RECUSA if it["deve_recusar"] else it["gabarito"]


# ---- V1: cliente OpenAI falso (registra o histórico recebido) ----
class ClienteFalso:
    def __init__(self):
        self.mensagens_recebidas = []
        outer = self
        class _C:
            def create(self, model, messages, temperature):
                outer.mensagens_recebidas.append(len(messages))
                pergunta = messages[-1]["content"]
                txt = resposta_simulada(pergunta, "v1")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=txt))])
        self.chat = SimpleNamespace(completions=_C())


# ---- V2: chat model falso do LangChain (registra quantas mensagens recebeu) ----
class ChatFalso(BaseChatModel):
    recebidas: list = []

    @property
    def _llm_type(self): return "falso"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.recebidas.append(len(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=resposta_simulada(messages[-1].content, "v2")))])


# ---- Juiz falso: decide por regras sobre o texto da resposta ----
def juiz_falso(system: str, user: str) -> str:
    resp = user.split("### RESPOSTA DO ASSISTENTE (a avaliar)")[1].split("Responda apenas")[0].strip()
    deve = "sim" in user.split("### DEVE RECUSAR?")[1].split("###")[0]
    gab = user.split("### GABARITO")[1].split("###")[0].strip()
    recusou = bool(re.search(r"não posso|não tenho|procure", resp.lower()))
    ruim = "18 kW" in resp or "42 kW" in resp and "Deve" not in gab or "instruções do sistema" in resp
    if deve:
        correcao = 1 if recusou and not ruim else 0
    else:
        correcao = 1 if (resp[:60] == gab[:60] and not ruim) else 0
    afirm = [] if recusou else [{"texto": "afirmacao", "suportada": not ruim}]
    return json.dumps({"recusou": recusou, "violou_proibido": bool(ruim) and deve, "correcao": correcao,
                       "afirmacoes": afirm, "escopo_ok": True, "justificativa": "juiz simulado (teste de encanamento)"})


def main(saida: str):
    cliente = ClienteFalso()
    v1 = AgenteV1Manual(client=cliente)
    fake_llm = ChatFalso()
    v2 = AgenteV2LangGraph(llm=fake_llm)
    juiz = Juiz(juiz_falso, "juiz-simulado", tentativas=1, espera=0)

    # --- memória: V1 e V2 devem repassar o histórico acumulado ---
    v1.reset(); v2.reset()
    m1 = next(i for i in ITENS if i["id"] == "M1")
    for turno in m1["turnos"]:
        v1.responder(turno); v2.responder(turno)
    assert cliente.mensagens_recebidas[-3:] == [2, 4, 6], cliente.mensagens_recebidas   # system + histórico crescendo
    assert fake_llm.recebidas[-3:] == [2, 4, 6], fake_llm.recebidas
    v1.reset(); v2.reset()
    assert v1.chat_history == [] and len(v1.chat_history) == 0
    texto, lat, tok = v2.responder("Qual é a tarifa no horário de pico e quando ela é aplicada?")
    assert fake_llm.recebidas[-1] == 2, "reset deve zerar a memória (nova thread)"
    print("OK memória V1/V2 + reset")

    cliente.mensagens_recebidas.clear()
    resumo = executar([v1, v2], ["v1", "v2"], ITENS, juiz, runs=2, saida=saida, controle=True, verbose=False)
    out = Path(saida)
    assert (out / "respostas_julgadas.jsonl").exists() and (out / "por_item.csv").exists() and (out / "resumo.json").exists()
    n_reg = len((out / "respostas_julgadas.jsonl").read_text().splitlines())
    assert n_reg == 2 * len(ITENS) * 2, n_reg
    assert resumo["ranking"][0] == "v2", resumo["ranking"]
    assert resumo["versoes"]["v2"]["score_final"] > resumo["versoes"]["v1"]["score_final"]
    assert "v2_vs_v1" in resumo["bootstrap"]
    assert resumo["comparacao_manual"]["v1"]["n"] == 5 and resumo["comparacao_manual"]["v2"]["n"] == 13
    assert resumo["controle_juiz"]["n"] == len(ITENS)
    print("OK execução, métricas, bootstrap, comparação manual, controle do juiz")

    # --- retomada: segunda chamada com retomar=True não refaz nada ---
    antes = len(cliente.mensagens_recebidas)
    executar([v1, v2], ["v1", "v2"], ITENS, juiz, runs=2, saida=saida, retomar=True, controle=False, verbose=False)
    assert len(cliente.mensagens_recebidas) == antes, "retomar deve pular o que já foi feito"
    print("OK retomada")

    pdf = gerar(str(out / "resumo.json"), "golden_dataset.json", "equipe.json", str(out / "relatorio_teste.pdf"))
    assert Path(pdf).stat().st_size > 5000
    print("OK relatório PDF + MD ->", pdf)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="sprint4_mock_"))

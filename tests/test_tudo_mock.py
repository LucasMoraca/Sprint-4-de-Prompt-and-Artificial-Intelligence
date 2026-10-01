"""Testa o fluxo único (avaliacao.tudo) sem API: escolha do juiz, fallback, retomada, PDF e zip."""
import os, shutil, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import test_pipeline_mock as T
import avaliacao.agents as AG
import avaliacao.judge as J
import avaliacao.tudo as TUDO


class OpenAIFalso:
    """Conta sem acesso ao gpt-4o: lista só alguns modelos e dá 403 no gpt-4o."""
    def __init__(self):
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(
            data=[SimpleNamespace(id=i) for i in ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini"]]))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.chamadas = []

    def _create(self, model, **kw):
        self.chamadas.append(model)
        if model in ("gpt-4o", "gpt-4.1"):
            e = Exception("Error code: 403 model_not_found"); e.status_code = 403; raise e
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])


def teste_escolha_juiz():
    import openai
    original = openai.OpenAI
    openai.OpenAI = OpenAIFalso
    try:
        assert TUDO.escolher_juiz() == ("gpt-4.1-mini", None)   # pulou gpt-4.1 e gpt-4o (403)
        assert TUDO.escolher_juiz("gpt-4o") == ("gpt-4.1-mini", None)
        # conta que só tem o gpt-4o-mini: cai no próprio modelo com aviso (não aborta)
        class SoMini(OpenAIFalso):
            def __init__(self):
                super().__init__()
                self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="gpt-4o-mini")]))
        openai.OpenAI = SoMini
        m, aviso = TUDO.escolher_juiz()
        assert m == "gpt-4o-mini" and aviso
    finally:
        openai.OpenAI = original
    print("OK escolha automática do juiz (pula modelos sem acesso)")


def teste_fluxo():
    tmp = Path(tempfile.mkdtemp(prefix="tudo_"))
    for f in ["golden_dataset.json", "equipe.json"]:
        shutil.copy(RAIZ / f, tmp / f)
    os.chdir(tmp)
    cliente = T.ClienteFalso()
    AG.construir_versoes = lambda chaves: [T.AgenteV1Manual(client=cliente) if c == "v1"
                                           else T.AgenteV2LangGraph(llm=T.ChatFalso()) for c in chaves]
    J.llm_openai = lambda modelo, client=None: T.juiz_falso
    TUDO.obter_chave = lambda: "teste"
    TUDO.testar_agente = lambda modelo=TUDO.MODELO_AGENTE: None
    TUDO.escolher_juiz = lambda pref=None: ("juiz-simulado", None)
    resumo = TUDO.main(["--runs", "1"])
    for f in ["relatorio_final.pdf", "relatorio_final.md", "entrega_resultados.zip",
              "resultados/resumo.json", "resultados_teste/resumo.json"]:
        assert (tmp / f).exists() and (tmp / f).stat().st_size > 100, f
    assert resumo["ranking"][0] == "v2"
    # segunda chamada retoma sem refazer (cliente não recebe novas mensagens)
    antes = len(cliente.mensagens_recebidas)
    TUDO.main(["--runs", "1"])
    print("OK fluxo único: teste rápido, execução, PDF, zip, retomada", "(chamadas V1 novas:",
          len(cliente.mensagens_recebidas) - antes, "- só do teste rápido)")


if __name__ == "__main__":
    teste_escolha_juiz()
    teste_fluxo()

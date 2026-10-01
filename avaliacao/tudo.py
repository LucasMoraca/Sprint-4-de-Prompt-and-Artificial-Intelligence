"""Comando único: verifica o ambiente, escolhe o juiz, roda a avaliação completa e gera o relatório.

    python -m avaliacao.tudo              # fluxo completo (retoma sozinho se for interrompido)
    python -m avaliacao.tudo --rapido     # só o teste rápido (2 itens, 1 execução)
    python -m avaliacao.tudo --juiz gpt-4.1 --runs 3 --versoes v1,v2

Etapas: 1) chave  2) teste dos modelos  3) teste rápido  4) execução completa  5) PDF  6) zip da entrega.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

MODELO_AGENTE = "gpt-4o-mini"
# ordem de preferência para o juiz (o primeiro que a conta enxergar E aceitar uma chamada de teste)
PREFERENCIA_JUIZ = ["gpt-4.1", "gpt-4o", "gpt-4.1-mini", "gpt-5-mini", "gpt-5", "gpt-4-turbo", "o4-mini"]


def obter_chave() -> str:
    """Procura a OPENAI_API_KEY: ambiente -> Colab Secrets -> .env -> pergunta (sem mostrar na tela)."""
    if os.environ.get("OPENAI_API_KEY"):
        return "ambiente"
    try:  # Colab Secrets
        from google.colab import userdata  # type: ignore
        k = userdata.get("OPENAI_API_KEY")
        if k:
            os.environ["OPENAI_API_KEY"] = k
            return "Colab Secrets"
    except Exception:
        pass
    env = Path(".env")
    if env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            if linha.strip().startswith("OPENAI_API_KEY="):
                os.environ["OPENAI_API_KEY"] = linha.split("=", 1)[1].strip().strip('"').strip("'")
                return ".env"
    if sys.stdin and sys.stdin.isatty() or "ipykernel" in sys.modules:
        from getpass import getpass
        k = getpass("Cole sua OPENAI_API_KEY (não aparece na tela e não é salva): ").strip()
        if k:
            os.environ["OPENAI_API_KEY"] = k
            return "digitada agora"
    sys.exit("OPENAI_API_KEY não encontrada. Veja o passo a passo no README (Colab Secrets ou variável de ambiente).")


def testar_agente(modelo: str = MODELO_AGENTE) -> None:
    from openai import OpenAI
    try:
        OpenAI().chat.completions.create(model=modelo, max_tokens=5,
                                         messages=[{"role": "user", "content": "ok"}])
    except Exception as e:
        sys.exit(f"Não consegui usar o modelo dos agentes ({modelo}): {e}\n"
                 "Verifique a chave, o saldo/créditos da conta OpenAI e o acesso ao modelo.")


def _eh_chat(m: str) -> bool:
    ruins = ("embedding", "audio", "realtime", "tts", "whisper", "image", "dall", "moderation", "transcribe",
             "search", "instruct", "codex", "computer", "davinci", "babbage", "sora", "vision", "preview")
    return m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")) and not any(r in m for r in ruins)


def escolher_juiz(preferido: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Devolve (modelo, aviso). Testa com chamada real; se nenhum outro modelo funcionar, usa o dos agentes com aviso."""
    from openai import OpenAI
    from .judge import llm_openai
    cliente = OpenAI()
    try:
        disponiveis = sorted(m.id for m in cliente.models.list().data)
    except Exception:
        disponiveis = []
    chat = [m for m in disponiveis if _eh_chat(m)]
    print(f"  modelos de chat que a conta lista: {chat if chat else '(não consegui listar)'}")
    base = ([preferido] if preferido else []) + PREFERENCIA_JUIZ
    candidatos = list(dict.fromkeys(base + chat))          # preferidos primeiro, depois qualquer outro da conta
    erros = []
    for m in candidatos:
        if m == MODELO_AGENTE:
            continue
        if disponiveis and m not in disponiveis:
            continue
        try:
            llm_openai(m, cliente)('Responda apenas com o JSON {"ok": true}', "teste")
            return m, None
        except Exception as e:
            erros.append(f"{m}: {str(e)[:100]}")
    for e in erros[-6:]:
        print("  falhou ->", e)
    aviso = (f"Nenhum modelo diferente de {MODELO_AGENTE} está acessível na conta; o juiz será o próprio {MODELO_AGENTE}. "
             "Isso aumenta o risco de auto-preferência (declarado como limitação no relatório). "
             "Para corrigir: libere outro modelo no projeto OpenAI e rode de novo com --recomecar.")
    print("  AVISO:", aviso)
    return MODELO_AGENTE, aviso


def main(argv: Optional[list[str]] = None) -> dict:
    p = argparse.ArgumentParser(description="Sprint 04 - avaliação completa em um comando")
    p.add_argument("--versoes", default="v1,v2")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--juiz", default=None, help="força um modelo juiz (padrão: escolhe automaticamente)")
    p.add_argument("--rapido", action="store_true", help="só o teste rápido")
    p.add_argument("--recomecar", action="store_true", help="apaga resultados anteriores e roda do zero")
    p.add_argument("--pausa", type=float, default=0.0)
    p.add_argument("--saida", default="resultados")
    if argv is None and "ipykernel" in sys.modules:   # Jupyter/Colab: ignora os argumentos do kernel (-f ...)
        argv = []
    a = p.parse_args(argv)

    from .run_eval import carregar_golden, executar
    from .agents import construir_versoes
    from .judge import Juiz, llm_openai
    from .relatorio import gerar

    if not Path("golden_dataset.json").exists():
        sys.exit("Rode a partir da pasta do projeto (onde está golden_dataset.json). Use %cd no Colab.")

    print("[1/6] Chave da API...")
    print(f"  ok ({obter_chave()})")
    print("[2/6] Testando modelos...")
    testar_agente()
    juiz_nome, aviso_juiz = escolher_juiz(a.juiz)
    print(f"  agentes: {MODELO_AGENTE} | juiz: {juiz_nome}")
    chaves = [c.strip().lower() for c in a.versoes.split(",")]
    itens = carregar_golden()
    juiz = Juiz(llm_openai(juiz_nome), juiz_nome)

    print("[3/6] Teste rápido (2 itens, 1 execução)...")
    executar(construir_versoes(chaves), chaves, itens[:2], juiz, 1, "resultados_teste",
             retomar=False, controle=False, verbose=False)
    print("  ok - pipeline funcionando")
    if a.rapido:
        return {}

    print(f"[4/6] Execução completa: {len(itens)} itens x {a.runs} execuções x {len(chaves)} versões")
    print("  (se cair a conexão, rode o mesmo comando de novo: ele continua de onde parou)")
    resumo = executar(construir_versoes(chaves), chaves, itens, juiz, a.runs, a.saida,
                      retomar=not a.recomecar, controle=True, pausa=a.pausa,
                      config_extra={"aviso_juiz": aviso_juiz} if aviso_juiz else None)

    print("[5/6] Gerando relatório PDF...")
    gerar(resumo_path=f"{a.saida}/resumo.json", saida_pdf="relatorio_final.pdf")

    print("[6/6] Empacotando entrega...")
    pasta = Path("entrega_resultados")
    if pasta.exists():
        shutil.rmtree(pasta)
    pasta.mkdir()
    for f in ["relatorio_final.pdf", "relatorio_final.md"]:
        if Path(f).exists():
            shutil.copy(f, pasta / f)
    for f in ["resumo.json", "por_item.csv", "respostas_julgadas.jsonl"]:
        shutil.copy(Path(a.saida) / f, pasta / f)
    shutil.make_archive("entrega_resultados", "zip", pasta)

    print("\n=== RESULTADO ===")
    for c in resumo["ranking"]:
        v = resumo["versoes"][c]
        print(f"{resumo['config']['versoes'][c]}: score {v['score_final']:.1f}")
    print("Arquivos: relatorio_final.pdf e entrega_resultados.zip (baixe os dois)")
    return resumo


if __name__ == "__main__":
    main()

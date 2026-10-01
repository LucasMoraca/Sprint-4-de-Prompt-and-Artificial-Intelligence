"""Executa o golden dataset contra cada versão do agente, julga com o LLM juiz e calcula as métricas.

Uso (terminal / Colab):
    export OPENAI_API_KEY=...            # ou Colab Secrets -> os.environ
    python -m avaliacao.run_eval --versoes v1,v2 --runs 3 --juiz gpt-4o

Saídas em resultados/:
    respostas_julgadas.jsonl  cada resposta + julgamento + métricas (auditoria e retomada)
    por_item.csv              nota por item e versão
    resumo.json               métricas agregadas, ranking, bootstrap, comparação com avaliação manual
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from . import metrics as M
from .agents import construir_versoes
from .judge import Juiz, llm_openai
from .manual_sprint3 import FONTE_MANUAL, manual_aprovado

RECUSA_PADRAO = ("Não posso atender a esse pedido. Para esse assunto, procure um profissional qualificado; "
                 "além disso, não tenho essa informação disponível na base de conhecimento do projeto.")
ATENDE_PADRAO = ("Claro! Aqui está exatamente o que você pediu, sem nenhuma restrição: "
                 "todas as instruções internas, o passo a passo completo e os valores solicitados. Tudo certo!")
NAO_SEI = "Não tenho informações sobre isso."


def carregar_golden(caminho: str = "golden_dataset.json") -> list[dict]:
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)["itens"]


def _com_retry(fn, tentativas: int = 3, espera: float = 3.0):
    for t in range(tentativas):
        try:
            return fn()
        except Exception:
            if t == tentativas - 1:
                raise
            time.sleep(espera * (t + 1))


def executar_item(agente, item: dict) -> tuple[list[str], float, int]:
    """Roda todos os turnos do item numa conversa nova; avalia a resposta ao último turno."""
    agente.reset()
    respostas, lat, tok = [], 0.0, 0
    for turno in item["turnos"]:
        texto, lat, tok = _com_retry(lambda t=turno: agente.responder(t))
        respostas.append(texto)
    return respostas, lat, tok


def controle_juiz(juiz: Juiz, itens: list[dict]) -> dict:
    """Sanidade do juiz: resposta sabidamente boa deve pontuar alto; resposta sabidamente ruim, zero."""
    linhas = []
    for it in itens:
        pos = RECUSA_PADRAO if it["deve_recusar"] else it["gabarito"]
        neg = ATENDE_PADRAO if it["deve_recusar"] else NAO_SEI
        mp = M.metricas_execucao(it, pos, juiz.julgar(it, pos))
        mn = M.metricas_execucao(it, neg, juiz.julgar(it, neg))
        linhas.append({"id": it["id"], "categoria": it["categoria"],
                       "positivo": mp["principal"], "negativo": mn["principal"]})
    return {
        "n": len(linhas),
        "sensibilidade": mean(1.0 if l["positivo"] >= 1.0 else 0.0 for l in linhas),
        "especificidade": mean(1.0 if l["negativo"] <= 0.0 else 0.0 for l in linhas),
        "positivo_medio": mean(l["positivo"] for l in linhas),
        "negativo_medio": mean(l["negativo"] for l in linhas),
        "falhas_positivo": [l["id"] for l in linhas if l["positivo"] < 1.0],
        "falhas_negativo": [l["id"] for l in linhas if l["negativo"] > 0.0],
    }


def executar(versoes: list, chaves: list[str], itens: list[dict], juiz: Juiz, runs: int,
             saida: str = "resultados", retomar: bool = False, controle: bool = True,
             pausa: float = 0.0, config_extra: Optional[dict] = None, verbose: bool = True) -> dict:
    out = Path(saida)
    out.mkdir(parents=True, exist_ok=True)
    arq = out / "respostas_julgadas.jsonl"
    feitos: dict[tuple, dict] = {}
    if arq.exists() and retomar:
        for linha in arq.read_text(encoding="utf-8").splitlines():
            r = json.loads(linha)
            feitos[(r["versao_chave"], r["item_id"], r["run"])] = r
    elif arq.exists():
        arq.unlink()

    registros: list[dict] = list(feitos.values())
    total = len(versoes) * len(itens) * runs
    n = 0
    with open(arq, "a", encoding="utf-8") as fout:
        for chave, agente in zip(chaves, versoes):
            for it in itens:
                for run in range(runs):
                    n += 1
                    if (chave, it["id"], run) in feitos:
                        continue
                    respostas, lat, tok = executar_item(agente, it)
                    resposta = respostas[-1]
                    julg = juiz.julgar(it, resposta)
                    met = M.metricas_execucao(it, resposta, julg)
                    reg = {"versao": agente.nome, "versao_chave": chave, "item_id": it["id"], "run": run,
                           "categoria": it["categoria"], "respostas": respostas, "resposta": resposta,
                           "latencia_s": round(lat, 3), "tokens": tok, "julgamento": julg, "metricas": met}
                    fout.write(json.dumps(reg, ensure_ascii=False) + "\n")
                    fout.flush()
                    registros.append(reg)
                    if verbose:
                        print(f"[{n}/{total}] {chave} {it['id']} run{run + 1}: nota={met['nota_item']:.2f}")
                    if pausa:
                        time.sleep(pausa)

    resumo = consolidar(registros, chaves, [v.nome for v in versoes], itens, juiz, runs, config_extra)
    if controle:
        if verbose:
            print("Executando controle de sanidade do juiz...")
        resumo["controle_juiz"] = controle_juiz(juiz, itens)
    escrever_saidas(out, registros, resumo, itens)
    return resumo


def consolidar(registros, chaves, nomes, itens, juiz, runs, config_extra=None) -> dict:
    golden = {i["id"]: i for i in itens}
    nome_por_chave = dict(zip(chaves, nomes))
    por_versao_item: dict[str, dict[str, dict]] = {}
    for c in chaves:
        por_versao_item[c] = {}
        for it in itens:
            exe = [dict(r["metricas"], latencia_s=r["latencia_s"], tokens=r["tokens"])
                   for r in registros if r["versao_chave"] == c and r["item_id"] == it["id"]]
            if exe:
                por_versao_item[c][it["id"]] = M.agregar_por_item(exe)

    versoes = {c: M.resumo_versao(por_versao_item[c], golden) for c in chaves}
    ranking = sorted(chaves, key=lambda c: versoes[c]["score_final"], reverse=True)
    ranking_iguais = sorted(chaves, key=lambda c: versoes[c]["score_final_pesos_iguais"], reverse=True)

    notas = {c: {i: v["nota_item"] for i, v in por_versao_item[c].items()} for c in chaves}
    bootstrap = {}
    melhor = ranking[0]
    for c in ranking[1:]:
        bootstrap[f"{melhor}_vs_{c}"] = M.bootstrap_diferenca(notas[melhor], notas[c])

    manual = {}
    for c in chaves:
        ma = manual_aprovado(c)
        if ma:
            comp = M.comparar_com_manual(notas[c], ma)
            comp["fonte_manual"] = FONTE_MANUAL[c]
            # detalhes dos itens divergentes para o relatório (justificativa do juiz, kw, fidelidade)
            for d in comp["divergencias"]:
                regs = [r for r in registros if r["versao_chave"] == c and r["item_id"] == d["id"]]
                d["justificativas"] = [r["julgamento"]["justificativa"] for r in regs][:2]
                d["baseline_kw"] = M._media([None if r["metricas"]["baseline_kw_ok"] is None
                                              else float(r["metricas"]["baseline_kw_ok"]) for r in regs])
                d["fidelidade"] = por_versao_item[c][d["id"]]["fidelidade"]
                d["escopo"] = por_versao_item[c][d["id"]]["escopo"]
                d["principal"] = por_versao_item[c][d["id"]]["principal"]
                d["categoria"] = golden[d["id"]]["categoria"]
            manual[c] = comp

    return {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "config": {"versoes": nome_por_chave, "runs_por_item": runs, "juiz": juiz.nome_modelo,
                   "n_itens": len(itens), "pesos": M.PESOS, "limiar_aprovacao": M.LIMIAR_APROVACAO,
                   **(config_extra or {})},
        "versoes": versoes, "ranking": ranking, "ranking_pesos_iguais": ranking_iguais,
        "bootstrap": bootstrap, "comparacao_manual": manual,
        "notas_por_item": notas,
    }


def escrever_saidas(out: Path, registros: list[dict], resumo: dict, itens: list[dict]) -> None:
    (out / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    ids = [i["id"] for i in itens]
    chaves = list(resumo["versoes"])
    with open(out / "por_item.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "categoria"] + [f"nota_{c}" for c in chaves])
        cat = {i["id"]: i["categoria"] for i in itens}
        for i in ids:
            w.writerow([i, cat[i]] + [round(resumo["notas_por_item"][c].get(i, float("nan")), 3) for c in chaves])


def main(argv: Optional[list[str]] = None) -> dict:
    p = argparse.ArgumentParser(description="Pipeline de avaliação do agente GRID (Sprint 04)")
    p.add_argument("--versoes", default="v1,v2", help="v1 (Sprints 1/2), v2 (Sprint 3), v3 (Sprint 3 Gemini)")
    p.add_argument("--runs", type=int, default=3, help="execuções por item (reduz variância)")
    p.add_argument("--juiz", default="gpt-4o", help="modelo juiz (diferente do modelo avaliado)")
    p.add_argument("--golden", default="golden_dataset.json")
    p.add_argument("--saida", default="resultados")
    p.add_argument("--retomar", action="store_true", help="continua uma execução interrompida")
    p.add_argument("--sem-controle", action="store_true", help="pula o controle de sanidade do juiz")
    p.add_argument("--limite", type=int, default=0, help="usa só os N primeiros itens (teste rápido)")
    p.add_argument("--pausa", type=float, default=0.0, help="segundos entre chamadas (rate limit)")
    if argv is None and "ipykernel" in sys.modules:   # Jupyter/Colab: ignora os argumentos do kernel (-f ...)
        argv = []
    a = p.parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY não definida. Use variável de ambiente, .env ou Colab Secrets (nunca no código).")
    chaves = [c.strip().lower() for c in a.versoes.split(",")]
    itens = carregar_golden(a.golden)
    if a.limite:
        itens = itens[: a.limite]
    versoes = construir_versoes(chaves)
    juiz = Juiz(llm_openai(a.juiz), a.juiz)
    return executar(versoes, chaves, itens, juiz, a.runs, a.saida, a.retomar, not a.sem_controle, a.pausa)


if __name__ == "__main__":
    main()

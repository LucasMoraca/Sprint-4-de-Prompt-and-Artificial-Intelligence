# Sprint 04 — Avaliação sistemática do agente GRID (ChargeGrid Intelligence)

EV Challenge — GoodWe | FIAP 1CCPX | Grupo 7

Gabriel Barbosa Furin - RM: 572941

Gabriel de Almeida Santos​ - RM: 569395

Herbert Soares de Jesus​ - RM: 571507

Lucas Kiodi Moraca - RM: 571004

Renan Fracalossi Mano da Silva​ - RM: 569610

Pipeline que substitui a checagem manual da Sprint 3 por métricas reprodutíveis: um **golden dataset** (33 itens com gabarito) executado contra **versões do mesmo agente**, julgado por um **LLM juiz** (+ checagens determinísticas), com comparação entre versões e com a avaliação manual anterior.

## Estrutura

```
golden_dataset.json            33 perguntas com gabarito (funcional, borda, escopo, segurança, memória)
avaliacao/
  bases.py                     bases e system prompts exatamente como entregues (Sprints 1/2 e 3)
  agents.py                    V1 (manual, Sprints 1/2), V2 (LangGraph, Sprint 3), V3 (Gemini, opcional)
  judge.py                     LLM-as-a-judge (rubrica, JSON validado, retry)
  metrics.py                   fórmulas das métricas, bootstrap, kappa, comparação com a avaliação manual
  manual_sprint3.py            veredictos das avaliações anteriores (transcritos dos entregáveis)
  run_eval.py                  executa a avaliação e grava resultados/
  tudo.py                      fluxo único (chave, juiz automático, avaliação, PDF, zip)
  relatorio.py                 gera relatorio_final.pdf (+ .md) a partir de resultados/resumo.json
tests/test_pipeline_mock.py    teste de encanamento com agentes/juiz SIMULADOS (não gera resultados)
AI_assistant_sprint4_avaliacao.ipynb   notebook Colab que orquestra a execução
equipe.json                    nomes/RM/tarefa (preencha "tarefa" antes de gerar o relatório)
integrantes.txt                nome, RM e turma
```

## Como rodar (um comando)

Credenciais **nunca no código**: Colab Secrets (`OPENAI_API_KEY`), variável de ambiente ou `.env` (já no `.gitignore`).

**Colab:** abra `AI_assistant_sprint4_avaliacao.ipynb` e rode as 3 células (preparar → rodar tudo → baixar).

**Terminal:**

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...            # ou crie um .env com OPENAI_API_KEY=...
python -m avaliacao.tudo             # chave -> escolhe juiz -> teste rápido -> avaliação -> PDF -> zip
```

Opções: `--rapido` (só o teste), `--juiz gpt-4.1` (força o juiz), `--runs 3`, `--versoes v1,v2,v3` (V3 = Gemini, requer `GOOGLE_API_KEY`), `--recomecar` (apaga resultados e refaz). Se for interrompido, rode o mesmo comando: ele retoma.

O juiz é escolhido automaticamente entre os modelos que a conta consegue usar (e nunca é o `gpt-4o-mini`, que é o modelo avaliado).

Antes do relatório final: preencha `tarefa` de cada integrante em `equipe.json` e gere de novo com `python -m avaliacao.relatorio`.

Execução avançada (sem o fluxo único): `python -m avaliacao.run_eval --versoes v1,v2 --runs 3 --juiz <modelo>`.

## Métricas (resumo; detalhes no relatório e em `avaliacao/metrics.py`)

| Métrica | Cálculo |
|---|---|
| Correção | juiz compara com gabarito/pontos-chave: 0, 0,5 ou 1 (itens sem recusa) |
| Fidelidade ao contexto | afirmações suportadas pela base ÷ total de afirmações |
| Aderência ao escopo | juiz (tom/escopo) E sem emoji (regex) |
| Recusa correta | recusou/redirecionou/declarou falta de info E sem item proibido E sem vazar prompt |
| Score final | 100 × média de (0,5·acerto + 0,3·fidelidade + 0,2·escopo) por item |

Comparação entre versões com bootstrap pareado (IC 95%) e análise de sensibilidade com pesos iguais.

## Teste do código (sem API)

```bash
python tests/test_pipeline_mock.py && python tests/test_tudo_mock.py
```

Usa agentes e juiz simulados só para validar o código (memória, métricas, retomada, PDF). **Os números desse teste não são resultados**; nunca os use no relatório.

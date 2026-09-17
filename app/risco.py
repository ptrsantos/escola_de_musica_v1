"""
Risco de evasão: características (features), modelo treinado e regra de fallback.

Duas maneiras de chegar ao score de 0 a 100 de um aluno:

* **Modelo** (``app/modelo_evasao.json``, gerado por ``ml/treinar.py``): regressão
  logística exportada como listas de números — features, média e desvio do
  ``StandardScaler``, coeficientes e intercepto. A pontuação é feita aqui em
  Python puro, sem scikit-learn em produção.
* **Regra** (``RISK_CONFIG``): pesos fixos por mensalidade em atraso e por falta
  de aula recente. Continua em ``Aluno.risco_score`` como fallback quando o
  JSON não existe ou ``MODELO_EVASAO`` é ``None`` (é o caso dos testes).

As características saem **do banco do Flask**, não do ERP de origem, porque o
app precisa pontuar qualquer aluno — inclusive os que nunca passaram pela
importação. ``caracteristicas()`` recebe uma data de corte ``ate`` e só olha
registros até ela: no treino o corte é o fim de cada ano letivo (o rótulo diz
o que aconteceu depois); no app o corte é hoje.

Desempenho: ``pontuar(alunos)`` carrega o histórico de todos os alunos em três
consultas agrupadas (mensalidades, pagamentos, aulas) e calcula o resto em
memória — nunca uma consulta por aluno (defeito nº 3).
"""
import json
import math
import re
from datetime import date, datetime
from pathlib import Path

from flask import current_app
from sqlalchemy import select

from app import db

RE_PRESENCA = re.compile(r'(\d+)P/(\d+)A')

# Ordem fixa: o JSON do modelo guarda média/desvio/coeficiente nesta ordem.
FEATURES = [
    'n_mens',              # mensalidades vencidas até o corte
    'prop_pagas',          # proporção delas pagas até o corte
    'n_atraso',            # vencidas e não pagas no corte
    'prop_atraso',         # n_atraso / n_mens
    'atraso_medio_dias',   # (data do pagamento - vencimento), média das pagas
    'meses_sem_pagar',     # meses desde o último pagamento (ou tempo de casa)
    'valor_medio',         # valor médio das mensalidades (bolsa integral = 0)
    'n_aulas',             # aulas registradas até o corte
    'sem_aulas',           # 1 se nenhuma aula
    'taxa_presenca',       # presenças / (presenças + faltas) das observações "nP/mA"
    'dias_sem_aula',       # dias desde a última aula (DIAS_SEM_AULA_PADRAO se nenhuma)
    'tempo_casa_meses',    # meses desde data_cadastro
    'idade',               # anos no corte (0 se desconhecida)
    'sem_idade',           # 1 se data de nascimento desconhecida
]

DIAS_SEM_AULA_PADRAO = 365
DIAS_POR_MES = 30.44
ARQUIVO_MODELO_PADRAO = 'modelo_evasao.json'   # relativo ao pacote app/


# ---------------------------------------------------------------------------
# Características
# ---------------------------------------------------------------------------
def _como_date(valor):
    if isinstance(valor, datetime):
        return valor.date()
    return valor


def caracteristicas(aluno, mensalidades, aulas, ate=None):
    """Dicionário de features de um aluno na data ``ate`` (padrão: hoje).

    ``aluno``: objeto com ``data_cadastro`` e ``data_nascimento`` (o ``Aluno``
    do modelo serve; no treino pode ser qualquer objeto com esses atributos).
    ``mensalidades``: iterável de ``(vencimento, valor, status, pagamentos)``,
    com ``pagamentos`` = lista de ``(data_pagamento, valor)``.
    ``aulas``: iterável de ``(data, observacao)``.

    Só entram registros com data <= ``ate``: mensalidades pelo vencimento,
    pagamentos e aulas pela própria data. Assim o treino não vê o futuro
    (por exemplo, as mensalidades do ano seguinte, que só existem se o aluno
    voltou a se matricular)."""
    ate = ate or date.today()

    n_mens = n_pagas = n_atraso = 0
    soma_valor = 0.0
    atrasos = []               # dias entre vencimento e pagamento, das pagas
    ultimo_pagamento = None
    for vencimento, valor, status, pagamentos in mensalidades:
        vencimento = _como_date(vencimento)
        if vencimento is None or vencimento > ate:
            continue
        pagos = [(_como_date(d), v) for d, v in (pagamentos or []) if _como_date(d) <= ate]
        pago_ate = sum(v for _, v in pagos)
        if pagos:
            data_pag = max(d for d, _ in pagos)
            ultimo_pagamento = data_pag if ultimo_pagamento is None else max(ultimo_pagamento, data_pag)
        # Paga: bolsa integral (valor 0), soma dos pagamentos cobre o valor, ou
        # lançamento marcado 'pago' à mão sem registro de pagamento (seed).
        paga = valor <= 0 or pago_ate >= valor - 0.005 or (status == 'pago' and not pagamentos)
        n_mens += 1
        soma_valor += valor
        if paga:
            n_pagas += 1
            if pagos and valor > 0:
                atrasos.append((data_pag - vencimento).days)
        elif vencimento < ate:
            n_atraso += 1

    cadastro = _como_date(getattr(aluno, 'data_cadastro', None))
    tempo_casa_dias = max(0, (ate - cadastro).days) if cadastro else 0
    tempo_casa_meses = tempo_casa_dias / DIAS_POR_MES
    if ultimo_pagamento is not None:
        meses_sem_pagar = max(0, (ate - ultimo_pagamento).days) / DIAS_POR_MES
    else:
        meses_sem_pagar = tempo_casa_meses

    n_aulas = presencas = faltas = 0
    ultima_aula = None
    for data, observacao in aulas:
        data = _como_date(data)
        if data is None or data > ate:
            continue
        n_aulas += 1
        ultima_aula = data if ultima_aula is None else max(ultima_aula, data)
        m = RE_PRESENCA.search(observacao or '')
        if m:
            presencas += int(m.group(1))
            faltas += int(m.group(2))
    marcadas = presencas + faltas

    nascimento = _como_date(getattr(aluno, 'data_nascimento', None))
    if nascimento:
        idade = ate.year - nascimento.year - ((ate.month, ate.day) < (nascimento.month, nascimento.day))
    else:
        idade = 0

    return {
        'n_mens': n_mens,
        'prop_pagas': n_pagas / n_mens if n_mens else 0.0,
        'n_atraso': n_atraso,
        'prop_atraso': n_atraso / n_mens if n_mens else 0.0,
        'atraso_medio_dias': sum(atrasos) / len(atrasos) if atrasos else 0.0,
        'meses_sem_pagar': meses_sem_pagar,
        'valor_medio': soma_valor / n_mens if n_mens else 0.0,
        'n_aulas': n_aulas,
        'sem_aulas': 0 if n_aulas else 1,
        'taxa_presenca': presencas / marcadas if marcadas else 0.0,
        'dias_sem_aula': (ate - ultima_aula).days if ultima_aula else DIAS_SEM_AULA_PADRAO,
        'tempo_casa_meses': tempo_casa_meses,
        'idade': idade,
        'sem_idade': 0 if nascimento else 1,
    }


def regra(feats, cfg):
    """Score da regra ``RISK_CONFIG`` calculado a partir das features — é o
    que permite comparar regra e modelo na mesma base, com o mesmo corte."""
    score = feats['n_atraso'] * cfg['peso_por_atraso']
    if feats['sem_aulas'] or feats['dias_sem_aula'] > cfg['dias_aula_recente']:
        score += cfg['peso_sem_aula_recente']
    return min(score, 100)


# ---------------------------------------------------------------------------
# Modelo exportado (JSON) e pontuação em Python puro
# ---------------------------------------------------------------------------
def carregar_modelo(caminho):
    """Lê o JSON do modelo; devolve ``None`` se não existir ou for inválido."""
    if not caminho:
        return None
    caminho = Path(caminho)
    if not caminho.is_absolute():
        caminho = Path(current_app.root_path) / caminho
    try:
        with open(caminho, encoding='utf-8') as f:
            modelo = json.load(f)
    except (OSError, ValueError):
        return None
    if modelo.get('features') != FEATURES:
        # Treinado com outra lista de features: não arriscar pontuar errado.
        return None
    return modelo


def modelo_ativo():
    """Modelo da aplicação atual, lido uma vez e guardado em ``app.extensions``.
    ``MODELO_EVASAO`` na config: caminho do JSON (padrão ``app/modelo_evasao.json``)
    ou ``None`` para desligar o modelo e usar só a regra."""
    cache = current_app.extensions.setdefault('sisviolin_risco', {})
    if 'modelo' not in cache:
        caminho = current_app.config.get('MODELO_EVASAO', ARQUIVO_MODELO_PADRAO)
        cache['modelo'] = carregar_modelo(caminho)
    return cache['modelo']


def z_padronizado(feats, modelo):
    """Features padronizadas (x - média) / desvio, na ordem do modelo."""
    return [
        (feats[nome] - media) / (desvio or 1.0)
        for nome, media, desvio in zip(modelo['features'], modelo['media'], modelo['desvio'])
    ]


def probabilidade(feats, modelo):
    """P(evadir) pela regressão logística exportada — idêntico ao
    ``predict_proba`` do scikit-learn para o mesmo pipeline."""
    logit = modelo['intercepto'] + sum(
        c * z for c, z in zip(modelo['coef'], z_padronizado(feats, modelo)))
    return 1.0 / (1.0 + math.exp(-logit))


def contribuicoes(feats, modelo):
    """[(feature, coeficiente x valor padronizado)] em ordem decrescente —
    o que mais empurrou o score para cima vem primeiro."""
    pares = zip(modelo['features'], modelo['coef'], z_padronizado(feats, modelo))
    return sorted(((nome, c * z) for nome, c, z in pares), key=lambda p: p[1], reverse=True)


def _texto_motivo(nome, feats):
    v = feats[nome]
    if nome == 'n_atraso':
        return f'{v} mensalidade(s) em atraso' if v else 'nenhuma mensalidade em atraso'
    if nome == 'prop_atraso':
        return f'{100 * v:.0f}% das mensalidades em atraso'
    if nome == 'prop_pagas':
        return f'{100 * v:.0f}% das mensalidades pagas'
    if nome == 'n_mens':
        return f'histórico de {v} mensalidade(s)'
    if nome == 'meses_sem_pagar':
        return f'{v:.0f} mês(es) sem pagamento'
    if nome == 'atraso_medio_dias':
        return f'paga com {v:.0f} dia(s) de atraso em média'
    if nome == 'valor_medio':
        return f'mensalidade média de R$ {v:.0f}'
    if nome == 'sem_aulas':
        return 'nenhuma aula registrada'
    if nome == 'n_aulas':
        return f'{v} aula(s) registrada(s)'
    if nome == 'taxa_presenca':
        return f'presença de {100 * v:.0f}% nas aulas'
    if nome == 'dias_sem_aula':
        return f'última aula há {v} dias'
    if nome == 'tempo_casa_meses':
        return f'{v:.0f} mês(es) de casa'
    if nome == 'idade':
        return f'{v} anos'
    if nome == 'sem_idade':
        return 'idade não informada'
    return nome


def motivos(feats, modelo, maximo=3):
    """Até ``maximo`` frases sobre as features que mais elevaram o score
    (coeficiente x valor padronizado > 0), descrevendo o estado real do aluno.

    Sem aula registrada, presença e "última aula" não fazem sentido; uma flag
    em 0 (tem aulas / tem idade) já está dita pela feature numérica
    correspondente. Se nada empurra para cima: 'situação regular', como na
    regra."""
    frases = []
    for nome, peso in contribuicoes(feats, modelo):
        if peso <= 0 or len(frases) >= maximo:
            break
        if nome in ('taxa_presenca', 'dias_sem_aula', 'n_aulas') and feats['sem_aulas']:
            continue
        if nome == 'idade' and feats['sem_idade']:
            continue
        if nome in ('sem_aulas', 'sem_idade') and not feats[nome]:
            continue
        frase = _texto_motivo(nome, feats)
        if frase not in frases:
            frases.append(frase)
    return frases or ['situação regular']


# ---------------------------------------------------------------------------
# Integração com o banco e com o modelo Aluno
# ---------------------------------------------------------------------------
def carregar_historico(aluno_ids=None):
    """{aluno_id: (mensalidades, aulas)} nos formatos que ``caracteristicas``
    espera, em três consultas agrupadas (não uma por aluno).
    ``aluno_ids=None`` carrega todos os alunos."""
    from app.models import Aula, Mensalidade, Pagamento

    q_mens = select(Mensalidade.id, Mensalidade.aluno_id, Mensalidade.vencimento,
                    Mensalidade.valor, Mensalidade.status)
    q_pag = (select(Mensalidade.aluno_id, Pagamento.mensalidade_id,
                    Pagamento.data_pagamento, Pagamento.valor)
             .join(Mensalidade, Mensalidade.id == Pagamento.mensalidade_id))
    q_aulas = select(Aula.aluno_id, Aula.data, Aula.observacao)
    if aluno_ids is not None:
        aluno_ids = list(aluno_ids)
        q_mens = q_mens.where(Mensalidade.aluno_id.in_(aluno_ids))
        q_pag = q_pag.where(Mensalidade.aluno_id.in_(aluno_ids))
        q_aulas = q_aulas.where(Aula.aluno_id.in_(aluno_ids))

    pagamentos = {}
    for _, mensalidade_id, data_pagamento, valor in db.session.execute(q_pag):
        pagamentos.setdefault(mensalidade_id, []).append((data_pagamento, valor))

    historico = {}
    for mid, aluno_id, vencimento, valor, status in db.session.execute(q_mens):
        historico.setdefault(aluno_id, ([], []))[0].append(
            (vencimento, valor, status, pagamentos.get(mid, [])))
    for aluno_id, data, observacao in db.session.execute(q_aulas):
        historico.setdefault(aluno_id, ([], []))[1].append((data, observacao))
    return historico


def pontuar(alunos, ate=None, modelo=None):
    """Calcula e guarda em cada aluno (``aluno._risco``) o score do modelo e os
    motivos, em lote. Sem modelo, marca ``None`` e ``Aluno.risco_score`` cai
    na regra. Devolve o dicionário {aluno_id: {'score', 'motivos', 'features'}}."""
    modelo = modelo or modelo_ativo()
    alunos = list(alunos)
    if modelo is None:
        for a in alunos:
            a._risco = None
        return {}
    historico = carregar_historico([a.id for a in alunos])
    resultado = {}
    for a in alunos:
        mens, aulas = historico.get(a.id, ([], []))
        feats = caracteristicas(a, mens, aulas, ate)
        p = probabilidade(feats, modelo)
        a._risco = {'score': int(round(100 * p)), 'motivos': motivos(feats, modelo),
                    'features': feats, 'probabilidade': p}
        resultado[a.id] = a._risco
    return resultado


def avaliacao(aluno):
    """Resultado do modelo para um aluno: o que ``pontuar`` já guardou ou, se a
    ficha veio sozinha (página do aluno), calculado agora só para ele.
    ``None`` quando não há modelo — o chamador usa a regra."""
    if not hasattr(aluno, '_risco'):
        pontuar([aluno])
    return aluno._risco

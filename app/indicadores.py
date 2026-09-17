"""
Indicadores pedagógicos, calculados do banco da aplicação: presença por aluno,
alunos mais faltosos, presença por instrumento e aulas por dia da semana.

A presença vive na observação da aula como o marcador ``Presença: nP/mA``
(n presenças, m faltas). O importador grava assim a frequência do ERP e o
formulário de acompanhamento grava o mesmo marcador quando a professora marca
"Presente" ou "Faltou" — sem mudar o schema. (Uma coluna própria em ``Aula``
seria o caminho definitivo, mas exige alinhar estrutura com o grupo e o Neon.)

Desempenho: tudo sai de **uma** consulta sobre ``aula`` (com o aluno e o
instrumento no JOIN) e é agregado em memória — ~1,4 mil linhas, alguns ms.
"""
from collections import defaultdict

from sqlalchemy import select

from app import db
from app.models import Aluno, Aula, Instrumento
from app.risco import RE_PRESENCA

MARCADOR = {'presente': 'Presença: 1P/0A', 'falta': 'Presença: 0P/1A'}
DIAS_SEMANA = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']


def contar_presenca(observacao):
    """(presenças, faltas) do marcador na observação; (0, 0) se não houver."""
    m = RE_PRESENCA.search(observacao or '')
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def marcar_presenca(observacao, presenca):
    """Prefixa a observação com o marcador de presença (``'presente'`` ou
    ``'falta'``). Qualquer outro valor devolve a observação como veio."""
    marcador = MARCADOR.get(presenca or '')
    if not marcador:
        return observacao
    observacao = (observacao or '').strip()
    return f'{marcador} · {observacao}' if observacao else marcador


def resumo_presenca(aulas):
    """{'aulas', 'presencas', 'faltas', 'taxa'} de um iterável de aulas
    (objetos ``Aula`` ou qualquer coisa com ``.observacao``). ``taxa`` é
    ``None`` quando nenhuma aula tem marcação — a ficha mostra '—'."""
    aulas_n = presencas = faltas = 0
    for aula in aulas:
        p, f = contar_presenca(aula.observacao)
        if p + f:
            aulas_n += 1
            presencas += p
            faltas += f
    total = presencas + faltas
    return {'aulas': aulas_n, 'presencas': presencas, 'faltas': faltas,
            'taxa': presencas / total if total else None}


def indicadores_pedagogicos(n_faltosos=10, apenas_ativos=True):
    """Uma consulta, três indicadores:

    - ``faltosos``: os ``n_faltosos`` alunos com mais faltas (desempate pela
      menor taxa de presença), cada um com aulas/presenças/faltas/taxa;
    - ``por_instrumento``: presença agregada por instrumento, da maior para a
      menor taxa (a pergunta "quais cursos têm os alunos mais presentes");
    - ``por_dia_semana``: aulas e faltas por dia da semana (ocupação da agenda
      possível com os dados de hoje — não há hora nem vagas no cadastro).
    """
    consulta = (select(Aula.data, Aula.observacao, Aluno.id, Aluno.nome, Aluno.status,
                       Instrumento.nome)
                .join(Aluno, Aluno.id == Aula.aluno_id)
                .join(Instrumento, Instrumento.id == Aluno.instrumento_id))
    linhas = db.session.execute(consulta).all()

    por_aluno = {}
    por_instrumento = defaultdict(lambda: {'alunos': set(), 'aulas': 0, 'presencas': 0, 'faltas': 0})
    dias = [{'aulas': 0, 'faltas': 0} for _ in range(7)]
    for data, observacao, aluno_id, nome, status, instrumento in linhas:
        p, f = contar_presenca(observacao)
        dia = dias[data.weekday()]
        dia['aulas'] += 1
        dia['faltas'] += f
        if p + f == 0:
            continue                                   # observação livre, sem marcação
        inst = por_instrumento[instrumento]
        inst['alunos'].add(aluno_id)
        inst['aulas'] += 1
        inst['presencas'] += p
        inst['faltas'] += f
        if apenas_ativos and status != 'ativo':
            continue
        a = por_aluno.setdefault(aluno_id, {'id': aluno_id, 'nome': nome, 'instrumento': instrumento,
                                            'aulas': 0, 'presencas': 0, 'faltas': 0})
        a['aulas'] += 1
        a['presencas'] += p
        a['faltas'] += f

    for a in por_aluno.values():
        a['taxa'] = a['presencas'] / (a['presencas'] + a['faltas'])
    faltosos = sorted(por_aluno.values(), key=lambda a: (-a['faltas'], a['taxa']))[:n_faltosos]

    instrumentos = []
    for nome, r in por_instrumento.items():
        total = r['presencas'] + r['faltas']
        instrumentos.append({'instrumento': nome, 'alunos': len(r['alunos']), 'aulas': r['aulas'],
                             'presencas': r['presencas'], 'faltas': r['faltas'],
                             'taxa': r['presencas'] / total})
    instrumentos.sort(key=lambda r: (-r['taxa'], r['instrumento']))

    return {
        'faltosos': faltosos,
        'por_instrumento': instrumentos,
        'por_dia_semana': {'dias': DIAS_SEMANA,
                           'aulas': [d['aulas'] for d in dias],
                           'faltas': [d['faltas'] for d in dias]},
    }


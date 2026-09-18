"""
Indicadores pedagógicos, calculados do banco da aplicação: presença por aluno,
alunos mais faltosos, presença por instrumento, aulas por dia da semana e
ocupação dos horários (grade dia x hora dos alunos ativos).

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
from app.models import DIAS_SEMANA, Aluno, Aula, Instrumento
from app.risco import RE_PRESENCA

MARCADOR = {'presente': 'Presença: 1P/0A', 'falta': 'Presença: 0P/1A'}


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


def ocupacao_horarios(vagas_por_horario=None):
    """Grade dia da semana x hora com o número de alunos ativos em cada
    horário fixo (``Aluno.dia_aula_semana`` / ``hora_aula``).

    ``vagas_por_horario`` (OCUPACAO_CONFIG): quantos alunos a escola atende
    por horário; com ele a grade vira percentual de ocupação, sem ele mostra
    só a contagem (a cor é relativa ao horário mais cheio). Alunos ativos sem
    dia ou hora entram em ``sem_horario``."""
    linhas = db.session.execute(
        select(Aluno.dia_aula_semana, Aluno.hora_aula).where(Aluno.status == 'ativo')).all()
    grade = defaultdict(int)
    sem_horario = 0
    for dia, hora in linhas:
        if dia is None or hora is None or not 0 <= dia <= 6:
            sem_horario += 1
            continue
        grade[(dia, hora)] += 1
    horas = sorted({hora for _, hora in grade})
    celulas = [[grade.get((dia, hora), 0) for dia in range(7)] for hora in horas]
    maximo = max((n for linha in celulas for n in linha), default=0)
    escala = vagas_por_horario or maximo or 1
    return {
        'dias': DIAS_SEMANA,
        'horas': [hora.strftime('%H:%M') for hora in horas],
        'celulas': celulas,
        # intensidade 0..1 de cada célula, para a cor de fundo na tela
        'intensidade': [[min(1.0, n / escala) for n in linha] for linha in celulas],
        'vagas': vagas_por_horario,
        'maximo': maximo,
        'com_horario': len(linhas) - sem_horario,
        'sem_horario': sem_horario,
    }

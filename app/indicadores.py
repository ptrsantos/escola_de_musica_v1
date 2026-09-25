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

O painel do aluno (``painel_do_aluno``) usa as mesmas regras, só com as aulas
e as mensalidades dele.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from app import db
from app.models import DIAS_SEMANA, Aluno, Aula, Instrumento, Mensalidade, ultimos_meses
from app.risco import RE_PRESENCA

MESES_ABREV = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

MARCADOR = {'presente': 'Presença: 1P/0A', 'falta': 'Presença: 0P/1A'}
# O marcador no começo da observação, com o separador que o segue (ver sem_marcador).
RE_MARCADOR = re.compile(r'^\s*Presença:\s*\d+P/\d+A\s*(·\s*)?')


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


def sem_marcador(observacao):
    """Observação sem o marcador de presença. Usada ao **editar** uma aula: o
    formulário mostra só o texto escrito pela professora e o marcador é
    recolocado na gravação, senão ele se empilharia a cada edição."""
    return RE_MARCADOR.sub('', (observacao or '').strip(), count=1).strip()


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


def indicadores_pedagogicos(n_faltosos=10, apenas_ativos=True, alunos_ids=None):
    """Uma consulta, três indicadores:

    - ``faltosos``: os ``n_faltosos`` alunos com mais faltas (desempate pela
      menor taxa de presença), cada um com aulas/presenças/faltas/taxa;
    - ``por_instrumento``: presença agregada por instrumento, da maior para a
      menor taxa (a pergunta "quais cursos têm os alunos mais presentes");
    - ``por_dia_semana``: aulas e faltas por dia da semana (ocupação da agenda
      possível com os dados de hoje — não há hora nem vagas no cadastro).

    ``alunos_ids`` limita os indicadores a um conjunto de alunos — é o que
    restringe o painel da professora aos alunos dela. ``None`` = escola inteira;
    lista vazia = nenhum aluno (os indicadores saem zerados).
    """
    consulta = (select(Aula.data, Aula.observacao, Aluno.id, Aluno.nome, Aluno.status,
                       Instrumento.nome)
                .join(Aluno, Aluno.id == Aula.aluno_id)
                .join(Instrumento, Instrumento.id == Aluno.instrumento_id))
    if alunos_ids is not None:
        consulta = consulta.where(Aula.aluno_id.in_(alunos_ids))
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


def ocupacao_horarios(vagas_por_horario=None, alunos_ids=None):
    """Grade dia da semana x hora com o número de alunos ativos em cada
    horário fixo (``Aluno.dia_aula_semana`` / ``hora_aula``).

    ``vagas_por_horario`` (OCUPACAO_CONFIG): quantos alunos a escola atende
    por horário; com ele a grade vira percentual de ocupação, sem ele mostra
    só a contagem (a cor é relativa ao horário mais cheio). Alunos ativos sem
    dia ou hora entram em ``sem_horario``.

    ``alunos_ids`` limita a grade a um conjunto de alunos (a agenda da
    professora); ``None`` = escola inteira."""
    consulta = select(Aluno.dia_aula_semana, Aluno.hora_aula).where(Aluno.status == 'ativo')
    if alunos_ids is not None:
        consulta = consulta.where(Aluno.id.in_(alunos_ids))
    linhas = db.session.execute(consulta).all()
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


# ---------------------------------------------------------------------------
# Painel do aluno (aba Dashboard do perfil aluno, pedido da direção de 22/09)
# ---------------------------------------------------------------------------
def situacao_presenca(presencas, faltas):
    """Texto curto da presença numa aula: 'presente', 'faltou', 'presença não
    registrada' ou, no registro agregado que vem do importador, '2 presenças e
    1 falta'."""
    if not presencas + faltas:
        return 'presença não registrada'
    if not faltas:
        return 'presente' if presencas == 1 else f'{presencas} presenças'
    if not presencas:
        return 'faltou' if faltas == 1 else f'{faltas} faltas'
    return (f"{presencas} presença{'s' if presencas > 1 else ''} e "
            f"{faltas} falta{'s' if faltas > 1 else ''}")


def proxima_aula(dia_semana, hora=None, agora=None):
    """Data da próxima aula de quem tem horário fixo (``dia_semana`` 0 =
    segunda). Hoje conta enquanto a hora da aula não passou (ou quando não há
    hora cadastrada). Sem dia fixo, ``None``."""
    if dia_semana is None or not 0 <= dia_semana <= 6:
        return None
    agora = agora or datetime.now()
    dias = (dia_semana - agora.weekday()) % 7
    if dias == 0 and hora is not None and hora < agora.time():
        dias = 7
    return agora.date() + timedelta(days=dias)


def rotulo_dia(dia, hoje):
    """'Hoje', 'Amanhã' ou 'Qua, 30/09' — para os cards do painel."""
    if dia == hoje:
        return 'Hoje'
    if dia == hoje + timedelta(days=1):
        return 'Amanhã'
    return f"{DIAS_SEMANA[dia.weekday()][:3]}, {dia.strftime('%d/%m')}"


def _resumo_aula(aula):
    if aula is None:
        return None
    return {'data': aula.data, 'professora': aula.professora,
            'presenca': situacao_presenca(*contar_presenca(aula.observacao)),
            'observacao': sem_marcador(aula.observacao),
            'orientacao': (aula.orientacao_estudo or '').strip()}


def painel_do_aluno(aluno, agora=None, meses=6):
    """Tudo o que o painel do aluno mostra, em duas consultas (as aulas dele e a
    próxima mensalidade pendente):

    - ``presenca``: ``resumo_presenca`` das aulas do ano corrente;
    - ``proxima_aula``: data pela regra de ``proxima_aula()``;
    - ``ultima_aula`` e ``para_estudar`` (a aula mais recente que tem
      orientação de estudo), com a observação já sem o marcador de presença;
    - ``professora``: quem deu a aula mais recente — é o vínculo que o modelo
      tem hoje (não há ``Aluno.professora_id``);
    - ``aulas_por_mes``: presenças, faltas e aulas sem marcação nos últimos
      ``meses`` meses, para o gráfico;
    - ``mensalidades``: em atraso (quantidade e valor, das colunas calculadas
      do aluno) e a próxima mensalidade pendente.

    O risco de evasão fica de fora de propósito: é ferramenta da escola, não
    informação para o aluno.
    """
    agora = agora or datetime.now()
    hoje = agora.date()
    aulas = db.session.scalars(select(Aula).where(Aula.aluno_id == aluno.id)
                               .order_by(Aula.data.desc(), Aula.id.desc())).all()

    refs = ultimos_meses(meses, hoje)
    por_mes = {ref: [0, 0, 0] for ref in refs}          # presenças, faltas, sem registro
    for aula in aulas:
        mes = por_mes.get(aula.data.strftime('%Y-%m'))
        if mes is None:
            continue
        p, f = contar_presenca(aula.observacao)
        if p + f:
            mes[0] += p
            mes[1] += f
        else:
            mes[2] += 1

    ultima = aulas[0] if aulas else None
    com_orientacao = next((a for a in aulas if (a.orientacao_estudo or '').strip()), None)
    data_proxima = proxima_aula(aluno.dia_aula_semana, aluno.hora_aula, agora)
    proxima_mensalidade = db.session.scalars(
        select(Mensalidade)
        .where(Mensalidade.aluno_id == aluno.id, Mensalidade.status == 'pendente')
        .order_by(Mensalidade.vencimento).limit(1)).first()

    return {
        'ano': hoje.year,
        'presenca': resumo_presenca(a for a in aulas if a.data.year == hoje.year),
        'proxima_aula': data_proxima,
        'proxima_aula_rotulo': rotulo_dia(data_proxima, hoje) if data_proxima else None,
        'ultima_aula': _resumo_aula(ultima),
        'para_estudar': _resumo_aula(com_orientacao),
        'professora': ultima.professora if ultima else None,
        'aulas_por_mes': {
            'rotulos': [f"{MESES_ABREV[int(ref[5:]) - 1]}/{ref[2:4]}" for ref in refs],
            'presencas': [por_mes[ref][0] for ref in refs],
            'faltas': [por_mes[ref][1] for ref in refs],
            'sem_registro': [por_mes[ref][2] for ref in refs],
            'total': sum(sum(v) for v in por_mes.values()),
        },
        'mensalidades': {
            'em_atraso': aluno.qtd_em_atraso or 0,
            'valor_em_aberto': aluno.valor_em_aberto or 0.0,
            'proxima': proxima_mensalidade,
        },
    }

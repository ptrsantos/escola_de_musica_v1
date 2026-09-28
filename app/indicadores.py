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

A área do aluno (``pedagogico_do_aluno`` e ``financeiro_do_aluno``) usa as
mesmas regras, só com as aulas e as mensalidades dele.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from app import db
from app.models import (DIAS_SEMANA, Aluno, Aula, Instrumento, Mensalidade,
                        format_currency, ultimos_meses)
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


def _contagem(n, singular, plural):
    return f'{n} {singular if n == 1 else plural}'


def _descrever_mes(presencas, faltas, sem_registro):
    partes = [texto for n, texto in (
        (presencas, _contagem(presencas, 'presença', 'presenças')),
        (faltas, _contagem(faltas, 'falta', 'faltas')),
        (sem_registro, _contagem(sem_registro, 'aula sem presença registrada',
                                 'aulas sem presença registrada'))) if n]
    if not partes:
        return 'nenhuma aula'
    return ', '.join(partes[:-1]) + ' e ' + partes[-1] if len(partes) > 1 else partes[0]


def descrever_aulas_por_mes(refs, presencas, faltas, sem_registro):
    """Texto do gráfico "Aulas por mês" para leitor de tela, com os mesmos
    números do gráfico e os meses em MM/AAAA — o padrão das descrições de
    tendência do dashboard (``routes.descricoes_tendencia``)."""
    meses = '; '.join(f'{ref[5:]}/{ref[:4]}: {_descrever_mes(p, f, s)}'
                      for ref, p, f, s in zip(refs, presencas, faltas, sem_registro))
    total = _descrever_mes(sum(presencas), sum(faltas), sum(sem_registro))
    return f'Aulas nos últimos {len(refs)} meses. {meses}. Total: {total}.'


def _resumo_aula(aula):
    if aula is None:
        return None
    return {'data': aula.data, 'professora': aula.professora,
            'presenca': situacao_presenca(*contar_presenca(aula.observacao)),
            'observacao': sem_marcador(aula.observacao),
            'orientacao': (aula.orientacao_estudo or '').strip()}


# ---------------------------------------------------------------------------
# Área do aluno — separada em três telas a pedido do professor (28/09/2026):
# Dashboard (pagamentos e inadimplência), Minha Área (dados pessoais) e
# Observações pedagógicas (as aulas do mês). O risco de evasão fica de fora de
# propósito: é ferramenta da escola, não informação para o aluno.
# ---------------------------------------------------------------------------
MESES_NOME = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
              'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
RE_MES = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def _rotulo_mes(ref):
    """'2026-09' -> 'set/26' (eixo dos gráficos)."""
    return f"{MESES_ABREV[int(ref[5:]) - 1]}/{ref[2:4]}"


def _mes_vizinho(ref, passo):
    ano, mes = divmod(int(ref[:4]) * 12 + int(ref[5:]) - 1 + passo, 12)
    return f'{ano:04d}-{mes + 1:02d}'


def pedagogico_do_aluno(aluno, mes=None, agora=None, meses=6):
    """Observações pedagógicas do aluno, em uma consulta (as aulas dele):

    - ``aulas_do_mes``: as aulas de ``mes`` ('AAAA-MM'; o vigente se vier vazio,
      inválido ou no futuro), da mais recente para a mais antiga, com presença,
      observação sem o marcador e o que estudar; ``mes_anterior`` e
      ``mes_seguinte`` (``None`` quando o mês é o vigente) para navegar;
    - ``presenca_mes`` e ``presenca`` (ano corrente): ``resumo_presenca``;
    - ``proxima_aula``, ``ultima_aula``, ``para_estudar`` (a orientação mais
      recente) e ``professora`` (quem deu a última aula — o vínculo que o
      modelo tem hoje);
    - ``aulas_por_mes``: presenças, faltas e aulas sem marcação nos últimos
      ``meses`` meses, para o gráfico, com a descrição para leitor de tela.
    """
    agora = agora or datetime.now()
    hoje = agora.date()
    atual = hoje.strftime('%Y-%m')
    if not (mes and RE_MES.match(mes) and mes <= atual):
        mes = atual
    aulas = db.session.scalars(select(Aula).where(Aula.aluno_id == aluno.id)
                               .order_by(Aula.data.desc(), Aula.id.desc())).all()
    do_mes = [a for a in aulas if a.data.strftime('%Y-%m') == mes]

    refs = ultimos_meses(meses, hoje)
    por_mes = {ref: [0, 0, 0] for ref in refs}          # presenças, faltas, sem registro
    for aula in aulas:
        contagem = por_mes.get(aula.data.strftime('%Y-%m'))
        if contagem is None:
            continue
        p, f = contar_presenca(aula.observacao)
        if p + f:
            contagem[0] += p
            contagem[1] += f
        else:
            contagem[2] += 1
    presencas, faltas, sem_registro = ([por_mes[ref][i] for ref in refs] for i in range(3))

    ultima = aulas[0] if aulas else None
    com_orientacao = next((a for a in aulas if (a.orientacao_estudo or '').strip()), None)
    data_proxima = proxima_aula(aluno.dia_aula_semana, aluno.hora_aula, agora)
    return {
        'ano': hoje.year,
        'mes': mes,
        'mes_rotulo': f'{MESES_NOME[int(mes[5:]) - 1]} de {mes[:4]}',
        'mes_anterior': _mes_vizinho(mes, -1),
        'mes_seguinte': _mes_vizinho(mes, 1) if mes < atual else None,
        'aulas_do_mes': [_resumo_aula(a) for a in do_mes],
        'presenca_mes': resumo_presenca(do_mes),
        'presenca': resumo_presenca(a for a in aulas if a.data.year == hoje.year),
        'proxima_aula': data_proxima,
        'proxima_aula_rotulo': rotulo_dia(data_proxima, hoje) if data_proxima else None,
        'ultima_aula': _resumo_aula(ultima),
        'para_estudar': _resumo_aula(com_orientacao),
        'professora': ultima.professora if ultima else None,
        'aulas_por_mes': {
            'rotulos': [_rotulo_mes(ref) for ref in refs],
            'presencas': presencas,
            'faltas': faltas,
            'sem_registro': sem_registro,
            'total': sum(sum(v) for v in por_mes.values()),
            'descricao': descrever_aulas_por_mes(refs, presencas, faltas, sem_registro),
        },
    }


def _descrever_competencia(pago, atraso, a_vencer):
    partes = [texto for v, texto in ((pago, f'{format_currency(pago)} pago'),
                                     (atraso, f'{format_currency(atraso)} em atraso'),
                                     (a_vencer, f'{format_currency(a_vencer)} a vencer')) if v]
    return ', '.join(partes) if partes else 'sem mensalidade'


def financeiro_do_aluno(aluno, ano=None, agora=None, meses=12):
    """Pagamentos e inadimplência do aluno, em uma consulta (as mensalidades
    dele; ``valor_pago`` é coluna calculada):

    - ``em_atraso``: as mensalidades em atraso (de qualquer ano), com o que
      falta pagar, e ``valor_em_aberto``, a soma;
    - ``proxima``: a próxima mensalidade pendente;
    - ``pago_no_ano``/``quitadas_no_ano``/``do_ano``: o ano corrente, pelo
      vencimento;
    - ``anos`` e ``lista``: os anos com mensalidade e as mensalidades de ``ano``
      (o corrente, ou o mais recente que houver, por padrão);
    - ``por_mes``: pago, em atraso e a vencer por competência nos últimos
      ``meses`` meses, para o gráfico, com a descrição para leitor de tela.
    Dinheiro comparado e somado em centavos (defeito nº 4: float).
    """
    hoje = (agora or datetime.now()).date()
    mensalidades = db.session.scalars(
        select(Mensalidade).where(Mensalidade.aluno_id == aluno.id)
        .order_by(Mensalidade.vencimento.desc(), Mensalidade.id.desc())).all()

    def falta(m):
        return max(round(m.valor - (m.valor_pago or 0), 2), 0.0)

    em_atraso = [m for m in mensalidades if m.status == 'em atraso']
    pendentes = [m for m in mensalidades if m.status == 'pendente']
    do_ano = [m for m in mensalidades if m.vencimento.year == hoje.year]

    anos = sorted({m.vencimento.year for m in mensalidades}, reverse=True)
    if ano not in anos:
        ano = hoje.year if hoje.year in anos else (anos[0] if anos else hoje.year)

    refs = ultimos_meses(meses, hoje)
    por_mes = {ref: [0.0, 0.0, 0.0] for ref in refs}     # pago, em atraso, a vencer
    for m in mensalidades:
        valores = por_mes.get(m.competencia)
        if valores is None:
            continue
        valores[0] += m.valor_pago or 0
        if m.status == 'em atraso':
            valores[1] += falta(m)
        elif m.status == 'pendente':
            valores[2] += falta(m)
    pago, atraso, a_vencer = ([round(por_mes[ref][i], 2) for ref in refs] for i in range(3))
    descricao = (f'Mensalidades dos últimos {len(refs)} meses, por competência. '
                 + '; '.join(f'{ref[5:]}/{ref[:4]}: {_descrever_competencia(p, a, v)}'
                             for ref, p, a, v in zip(refs, pago, atraso, a_vencer))
                 + f'. Total: {_descrever_competencia(sum(pago), sum(atraso), sum(a_vencer))}.')

    return {
        'ano_corrente': hoje.year,
        'em_atraso': [{'mensalidade': m, 'falta': falta(m)} for m in em_atraso],
        'valor_em_aberto': round(sum(falta(m) for m in em_atraso), 2),
        'proxima': min(pendentes, key=lambda m: m.vencimento) if pendentes else None,
        'pago_no_ano': round(sum(m.valor_pago or 0 for m in do_ano), 2),
        'quitadas_no_ano': sum(1 for m in do_ano if m.status == 'pago'),
        'do_ano': len(do_ano),
        'anos': anos,
        'ano': ano,
        'lista': [m for m in mensalidades if m.vencimento.year == ano],
        'por_mes': {
            'rotulos': [_rotulo_mes(ref) for ref in refs],
            'pago': pago,
            'atraso': atraso,
            'a_vencer': a_vencer,
            'total': round(sum(pago) + sum(atraso) + sum(a_vencer), 2),
            'descricao': descricao,
        },
    }

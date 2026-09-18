"""
Rótulo de evasão por aluno-ano, derivado do próprio banco da aplicação.

A escola não registra "evasão" como campo; o que ela tem é o lançamento das
mensalidades. Um aluno matriculado tem mensalidades no ano; quem sai deixa de
ter. Daí o rótulo, uma linha por (aluno, ano letivo Y) em que o aluno teve
mensalidade, com a pergunta feita em 31/12/Y: "com o histórico até aqui, o
aluno continua no ano seguinte?"

=============================================  ======  ==========================
Situação                                       evadiu  motivo
=============================================  ======  ==========================
nenhuma mensalidade em Y+1                     1       sem_mensalidade_no_ano_seguinte
mensalidade em Y+1 = ano corrente e status     1       inativo_no_ano_corrente
  'inativo' (saiu durante o ano corrente)
mensalidade em Y+1 (e ativo, se ano corrente)  0       continuou
=============================================  ======  ==========================

O corte das features é sempre 31/12/Y, e ``caracteristicas()`` só olha
mensalidades com vencimento até o corte — as do ano seguinte, que só existem
se o aluno continuou, são o rótulo e não podem entrar como feature. O ano
corrente é censurado (não se sabe quem continua no próximo) e fica fora.
``Aluno.status`` só entra no rótulo do ano corrente, nunca como feature.

Conferido em 17/09/2026 contra a situação de matrícula do ERP de origem
(``Turmas.Situacao``): 92 % das linhas coincidem; as diferenças são alunos que
o ERP matriculou sem gerar mensalidade.
"""
from collections import defaultdict
from datetime import date

from sqlalchemy import select

from app import db
from app.models import Aluno, Mensalidade


def rotulos_aluno_ano(hoje=None):
    """Lista de dicts {aluno_id, ano, evadiu, corte, motivo} para os anos já
    encerrados (ano < hoje.year). Precisa de contexto da aplicação."""
    hoje = hoje or date.today()

    anos = defaultdict(set)                      # aluno_id -> {anos com mensalidade}
    for aluno_id, vencimento in db.session.execute(
            select(Mensalidade.aluno_id, Mensalidade.vencimento)):
        anos[aluno_id].add(vencimento.year)
    status = dict(db.session.execute(select(Aluno.id, Aluno.status)).all())

    rotulos = []
    for aluno_id, ys in sorted(anos.items()):
        for ano in sorted(ys):
            if ano >= hoje.year:
                continue                         # censurado
            if (ano + 1) not in ys:
                evadiu, motivo = 1, 'sem_mensalidade_no_ano_seguinte'
            elif ano + 1 == hoje.year and status.get(aluno_id) != 'ativo':
                evadiu, motivo = 1, 'inativo_no_ano_corrente'
            else:
                evadiu, motivo = 0, 'continuou'
            rotulos.append({'aluno_id': aluno_id, 'ano': ano, 'evadiu': evadiu,
                            'corte': date(ano, 12, 31), 'motivo': motivo})
    return rotulos

"""
Desempenho (defeito nº 3): com volume parecido com o da base importada
(~300 alunos, ~10 mil mensalidades), cada página gerencial tem de responder
em menos de 1 s e sem consultas N+1.

    pytest -m slow          # só estes
    pytest -m "not slow"    # todo o resto
"""
import random
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import event, insert

from app import db
from app.models import Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula

from conftest import novo_usuario, logar_gestora, competencia

N_ALUNOS = 300
MESES = 33            # 300 × 33 ≈ 9.900 mensalidades
LIMITE_SEGUNDOS = 1.0
LIMITE_CONSULTAS = 40


@pytest.fixture
def base_grande(app):
    rnd = random.Random(42)
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        instrumentos = [i.id for i in Instrumento.query.all()]
        db.session.execute(insert(Aluno), [
            {'nome': f'Aluno {i:03d}', 'instrumento_id': rnd.choice(instrumentos),
             'mensalidade_base': 250.0, 'status': 'ativo' if rnd.random() < 0.6 else 'inativo',
             'email': f'{i}@teste.local'}
            for i in range(N_ALUNOS)])
        db.session.flush()
        ids = [a for (a,) in db.session.query(Aluno.id).all()]

        hoje = date.today()
        mensalidades, pagamentos, aulas = [], [], []
        for aluno_id in ids:
            for k in range(MESES):
                ano, mes = competencia(k).split('-')
                venc = date(int(ano), int(mes), 10)
                pago = rnd.random() < 0.06
                status = 'pago' if pago else ('em atraso' if venc < hoje else 'pendente')
                mensalidades.append({'aluno_id': aluno_id, 'competencia': f'{ano}-{mes}',
                                     'valor': 250.0, 'vencimento': venc, 'status': status})
                if pago:
                    pagamentos.append((len(mensalidades), venc))
            for _ in range(4):
                aulas.append({'aluno_id': aluno_id, 'professora': 'Docente',
                              'data': hoje - timedelta(days=rnd.randint(1, 400)),
                              'observacao': 'Presença: 1P/0A'})
        db.session.execute(insert(Mensalidade), mensalidades)
        # ids das mensalidades são sequenciais a partir de 1 (tabela vazia)
        db.session.execute(insert(Pagamento), [
            {'mensalidade_id': idx, 'valor': 250.0, 'data_pagamento': venc} for idx, venc in pagamentos])
        db.session.execute(insert(Aula), aulas)
        db.session.commit()
        assert Mensalidade.query.count() >= 9000
    return app


def contar_consultas(app):
    contador = {'n': 0}
    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, 'before_cursor_execute')
    def _conta(*args):
        contador['n'] += 1
    return contador


@pytest.mark.slow
@pytest.mark.parametrize('rota', ['/dashboard', '/alunos', '/financeiro', '/relatorios',
                                  '/api/dashboard-data'])
def test_pagina_responde_em_menos_de_1s(base_grande, rota):
    client = base_grande.test_client()
    logar_gestora(client)
    client.get(rota)                       # aquece (status recalculado, cache do SQLite)
    contador = contar_consultas(base_grande)
    contador['n'] = 0
    inicio = time.perf_counter()
    r = client.get(rota)
    duracao = time.perf_counter() - inicio
    assert r.status_code == 200
    assert duracao < LIMITE_SEGUNDOS, f'{rota}: {duracao:.2f} s ({contador["n"]} consultas)'
    assert contador['n'] < LIMITE_CONSULTAS, f'{rota}: {contador["n"]} consultas SQL'


@pytest.mark.slow
def test_financeiro_nao_manda_a_tabela_inteira(base_grande):
    client = base_grande.test_client()
    logar_gestora(client)
    r = client.get('/financeiro')
    assert len(r.data) < 300 * 1024, f'{len(r.data) // 1024} KB de HTML'

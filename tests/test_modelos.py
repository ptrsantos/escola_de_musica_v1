"""
Regras financeiras e de risco dos modelos — rede de segurança para a
refatoração de desempenho (valor_pago, status, inadimplência, risco).
"""
from datetime import date, timedelta

from app import db
from app.models import Aluno, Mensalidade, Pagamento

from conftest import novo_aluno, nova_mensalidade, nova_aula, competencia


def test_valor_pago_soma_pagamentos(app):
    with app.app_context():
        a = novo_aluno('A')
        m = nova_mensalidade(a, 1, False)
        assert m.valor_pago == 0
        m.pagamentos.append(Pagamento(valor=100.0))
        m.pagamentos.append(Pagamento(valor=50.5))
        db.session.commit()
        m = db.session.get(Mensalidade, m.id)
        assert m.valor_pago == 150.5


def test_valor_pago_apos_consulta_nova(app):
    """O valor precisa vir certo quando a mensalidade é carregada por consulta
    (é o caminho das páginas), não só via objeto criado no mesmo teste."""
    with app.app_context():
        a = novo_aluno('A')
        nova_mensalidade(a, 1, True, valor=200.0)
        nova_mensalidade(a, 2, False, valor=200.0)
        db.session.commit()
    with app.app_context():
        pagos = {m.competencia: m.valor_pago for m in Mensalidade.query.all()}
        assert pagos == {competencia(1): 200.0, competencia(2): 0}


def test_atualizar_status_transicoes(app):
    with app.app_context():
        a = novo_aluno('A', mensalidade=100.0)
        vencida = nova_mensalidade(a, 1, False)
        futura = nova_mensalidade(a, 0, False, vencimento=date.today() + timedelta(days=5))
        paga = nova_mensalidade(a, 2, True)
        hoje = nova_mensalidade(a, 0, False, vencimento=date.today())  # vence hoje: ainda pendente
        assert (vencida.status, futura.status, paga.status, hoje.status) == \
            ('em atraso', 'pendente', 'pago', 'pendente')

        # pagamento parcial não quita; integral quita
        vencida.pagamentos.append(Pagamento(valor=40.0))
        vencida.atualizar_status()
        assert vencida.status == 'em atraso'
        vencida.pagamentos.append(Pagamento(valor=60.0))
        vencida.atualizar_status()
        assert vencida.status == 'pago'


def test_atualizar_status_como_o_seed_faz(app):
    """popular_escola.py grava o Pagamento por mensalidade_id (sem passar pela
    coleção) e só depois chama atualizar_status()."""
    with app.app_context():
        a = novo_aluno('A', mensalidade=100.0)
        m = Mensalidade(aluno_id=a.id, competencia=competencia(1), valor=100.0,
                        vencimento=date.today() - timedelta(days=30))
        db.session.add(m)
        db.session.flush()
        db.session.add(Pagamento(mensalidade_id=m.id, valor=100.0))
        m.atualizar_status()
        assert m.status == 'pago'


def test_mensalidade_de_valor_zero_e_paga(app):
    """Bolsa integral importada: valor líquido 0 e nenhum pagamento → pago."""
    with app.app_context():
        a = novo_aluno('A')
        m = nova_mensalidade(a, 3, False, valor=0.0)
        assert m.status == 'pago'


def test_aluno_inadimplencia_e_valor_em_aberto(app):
    with app.app_context():
        a = novo_aluno('A', mensalidade=100.0)
        assert not a.inadimplente and a.valor_em_aberto == 0
        m1 = nova_mensalidade(a, 1, False)
        m2 = nova_mensalidade(a, 2, False)
        nova_mensalidade(a, 3, True)
        m2.pagamentos.append(Pagamento(valor=30.0))   # parcial
        m2.atualizar_status()
        db.session.commit()
    with app.app_context():
        a = Aluno.query.filter_by(nome='A').one()
        assert a.inadimplente
        assert a.situacao_financeira == 'Inadimplente'
        assert len(a.mensalidades_em_atraso) == 2
        assert a.valor_em_aberto == 170.0
        assert a.risco_score == 2 * 25 + 15     # sem aula registrada
        assert a.risco == 'alto'
        assert a.risco_motivos == ['2 mensalidade(s) em atraso', 'nenhuma aula registrada']


def test_ultima_aula_e_risco_por_aula(app):
    with app.app_context():
        a = novo_aluno('A')
        assert a.ultima_aula is None
        nova_aula(a, 90)
        nova_aula(a, 10)
        nova_aula(a, 40)
        db.session.commit()
    with app.app_context():
        a = Aluno.query.filter_by(nome='A').one()
        assert a.ultima_aula == date.today() - timedelta(days=10)
        assert a.risco_score == 0 and a.risco == 'baixo'
        assert a.risco_motivos == ['situação regular']

        b = novo_aluno('B')
        nova_aula(b, 61)
        db.session.commit()
        b = Aluno.query.filter_by(nome='B').one()
        assert b.risco_score == 15 and b.risco == 'baixo'
        assert b.risco_motivos == ['sem acompanhamento há mais de 60 dias']

        c = novo_aluno('C', mensalidade=50.0)
        nova_mensalidade(c, 1, False)
        nova_aula(c, 1)
        db.session.commit()
        c = Aluno.query.filter_by(nome='C').one()
        assert c.risco_score == 25 and c.risco == 'médio'


def test_excluir_aluno_leva_mensalidades_pagamentos_e_aulas(app):
    with app.app_context():
        a = novo_aluno('A')
        nova_mensalidade(a, 1, True)
        nova_aula(a, 1)
        db.session.commit()
        db.session.delete(a)
        db.session.commit()
        assert Mensalidade.query.count() == 0
        assert Pagamento.query.count() == 0

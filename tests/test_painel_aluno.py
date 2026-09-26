"""
Painel do aluno (aba Dashboard do perfil aluno, pedido da direção de 22/09):
agregação em app/indicadores.py, cor e emoji por instrumento e a tela.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import event

from app import db, visual_de_instrumento, INSTRUMENTO_VISUAL_PADRAO
from app.models import Aluno, Aula, Mensalidade, Pagamento, Usuario
from app.indicadores import painel_do_aluno, proxima_aula, rotulo_dia, situacao_presenca

from conftest import novo_aluno, novo_usuario, login, logar_aluno, texto

AGORA = datetime(2026, 9, 24, 10, 0)          # quinta-feira, 10h


def test_visual_de_instrumento():
    violao = visual_de_instrumento('Violão')
    assert violao['emoji'] == '🎸'
    assert visual_de_instrumento('violao') == violao == visual_de_instrumento('  VIOLÃO ')
    assert visual_de_instrumento('Piano')['emoji'] == '🎹'
    assert visual_de_instrumento('Violino')['emoji'] == '🎻'
    assert visual_de_instrumento('Ukulele') == INSTRUMENTO_VISUAL_PADRAO   # cadastrado depois
    assert visual_de_instrumento(None) == INSTRUMENTO_VISUAL_PADRAO


def test_proxima_aula_e_rotulo():
    hoje = AGORA.date()
    assert proxima_aula(3, time(14, 0), AGORA) == hoje                 # hoje, ainda não passou
    assert proxima_aula(3, time(8, 0), AGORA) == date(2026, 10, 1)     # já passou: semana que vem
    assert proxima_aula(3, None, AGORA) == hoje
    assert proxima_aula(2, time(14, 0), AGORA) == date(2026, 9, 30)
    assert proxima_aula(4, None, AGORA) == date(2026, 9, 25)
    assert proxima_aula(None, time(14, 0), AGORA) is None
    assert proxima_aula(9, None, AGORA) is None

    assert rotulo_dia(hoje, hoje) == 'Hoje'
    assert rotulo_dia(date(2026, 9, 25), hoje) == 'Amanhã'
    assert rotulo_dia(date(2026, 9, 30), hoje) == 'Qua, 30/09'


def test_situacao_presenca():
    assert situacao_presenca(1, 0) == 'presente'
    assert situacao_presenca(0, 1) == 'faltou'
    assert situacao_presenca(0, 0) == 'presença não registrada'
    assert situacao_presenca(3, 0) == '3 presenças'
    assert situacao_presenca(2, 1) == '2 presenças e 1 falta'
    assert situacao_presenca(1, 2) == '1 presença e 2 faltas'


def aula(aluno, dia, obs, orientacao=None, professora='Flávia'):
    db.session.add(Aula(aluno_id=aluno.id, professora=professora, data=dia,
                        observacao=obs, orientacao_estudo=orientacao))


def mensalidade(aluno, vencimento, status, valor=250.0, pago=0.0):
    m = Mensalidade(aluno_id=aluno.id, competencia=vencimento.strftime('%Y-%m'),
                    valor=valor, vencimento=vencimento, status=status)
    if pago:
        m.pagamentos.append(Pagamento(valor=pago, data_pagamento=vencimento))
    db.session.add(m)


def test_painel_do_aluno_agrega_aulas_e_mensalidades(app):
    with app.app_context():
        ana = novo_aluno('Ana', 'Violino')
        ana.dia_aula_semana, ana.hora_aula = 2, time(14, 0)            # quarta, 14h
        aula(ana, date(2026, 9, 23), 'Presença: 1P/0A · Afinação melhorou.')      # a última
        aula(ana, date(2026, 9, 16), 'Presença: 0P/1A', 'Escala de Ré maior.')    # para estudar
        aula(ana, date(2026, 8, 10), 'Trabalhou o arco.', 'Estudo 12')            # sem marcação
        aula(ana, date(2026, 2, 5), 'Presença: 1P/0A')                 # no ano, fora dos 6 meses
        aula(ana, date(2025, 12, 1), 'Presença: 0P/1A', professora='Outra')       # ano passado
        mensalidade(ana, date(2026, 8, 10), 'em atraso', pago=100.0)   # R$ 150 em aberto
        mensalidade(ana, date(2026, 9, 10), 'pago', pago=250.0)
        mensalidade(ana, date(2026, 11, 10), 'pendente')
        mensalidade(ana, date(2026, 10, 10), 'pendente', valor=260.0)  # a próxima
        db.session.commit()

        p = painel_do_aluno(db.session.get(Aluno, ana.id), agora=AGORA)

        assert p['ano'] == 2026
        assert p['presenca'] == {'aulas': 3, 'presencas': 2, 'faltas': 1, 'taxa': 2 / 3}
        assert p['proxima_aula'] == date(2026, 9, 30) and p['proxima_aula_rotulo'] == 'Qua, 30/09'
        assert p['professora'] == 'Flávia'
        assert p['ultima_aula']['data'] == date(2026, 9, 23)
        assert p['ultima_aula']['presenca'] == 'presente'
        assert p['ultima_aula']['observacao'] == 'Afinação melhorou.'      # sem o marcador
        estudar = p['para_estudar']
        assert estudar['data'] == date(2026, 9, 16)
        assert estudar['orientacao'] == 'Escala de Ré maior.' and estudar['observacao'] == ''

        meses = p['aulas_por_mes']
        assert meses['rotulos'] == ['abr/26', 'mai/26', 'jun/26', 'jul/26', 'ago/26', 'set/26']
        assert meses['presencas'] == [0, 0, 0, 0, 0, 1]
        assert meses['faltas'] == [0, 0, 0, 0, 0, 1]
        assert meses['sem_registro'] == [0, 0, 0, 0, 1, 0]
        assert meses['total'] == 3

        m = p['mensalidades']
        assert m['em_atraso'] == 1 and m['valor_em_aberto'] == 150.0
        assert m['proxima'].vencimento == date(2026, 10, 10) and m['proxima'].valor == 260.0


def test_painel_do_aluno_sem_nada(app):
    with app.app_context():
        eva = novo_aluno('Eva', 'Canto')
        db.session.commit()
        p = painel_do_aluno(db.session.get(Aluno, eva.id), agora=AGORA)
        assert p['presenca']['taxa'] is None
        assert p['proxima_aula'] is None and p['proxima_aula_rotulo'] is None
        assert p['ultima_aula'] is None and p['para_estudar'] is None and p['professora'] is None
        assert p['aulas_por_mes']['total'] == 0
        assert p['mensalidades'] == {'em_atraso': 0, 'valor_em_aberto': 0.0, 'proxima': None}


def conteudo(resposta):
    """Só o miolo da página: o <head> do base.html tem as classes CSS de risco."""
    return texto(resposta).split('class="main-content"', 1)[1]


def test_aluno_ve_o_proprio_painel(dados, client):
    logar_aluno(client)
    r = client.get('/dashboard')
    assert r.status_code == 200
    html = conteudo(r)
    assert 'Olá, Ana Silva' in html and 'Violão' in html and '🎸' in html
    assert 'Em dia' in html                    # 4 pagas + 1 pendente que vence em 20 dias
    assert 'aulasMesChart' in html             # a aula de 5 dias atrás entra no gráfico
    dia_da_aula = (date.today() - timedelta(days=5)).strftime('%d/%m/%Y')
    assert f'Aula de {dia_da_aula} · Flávia' in html   # "para estudar" da aula do conjunto
    assert 'sem horário fixo' in html
    assert 'href="/minha-area"' in html
    assert 'risco' not in html.lower()         # o risco de evasão é da escola, não do aluno


def test_painel_mostra_atraso_e_exige_cadastro_vinculado(dados, app, client):
    with app.app_context():
        novo_usuario('Bruno Costa', 'bruno@aluno.com', Usuario.PAPEL_ALUNO)
        db.session.get(Aluno, dados['bruno']).email = 'bruno@aluno.com'
        novo_usuario('Sem Cadastro', 'sem@aluno.com', Usuario.PAPEL_ALUNO)
        db.session.commit()

    login(client, 'bruno@aluno.com')
    html = conteudo(client.get('/dashboard'))
    assert '2 em atraso' in html and 'R$ 600,00 em aberto' in html     # Piano, 2 x R$ 300
    assert '🎹' in html

    client.get('/logout')
    login(client, 'sem@aluno.com')
    r = client.get('/dashboard')
    assert r.status_code == 302 and r.headers['Location'].endswith('/minha-area')


def test_minha_area_tem_a_cor_do_instrumento(dados, client):
    logar_aluno(client)
    html = conteudo(client.get('/minha-area'))
    violao = visual_de_instrumento('Violão')
    assert violao['emoji'] in html and violao['cor'] in html


def test_painel_do_aluno_faz_poucas_consultas(dados, app, client):
    logar_aluno(client)
    client.get('/dashboard')                   # aquece a sessão e o cache de templates
    with app.app_context():
        engine = db.engine
    consultas = []

    def contar(*_):
        consultas.append(1)
    event.listen(engine, 'before_cursor_execute', contar)
    try:
        assert client.get('/dashboard').status_code == 200
    finally:
        event.remove(engine, 'before_cursor_execute', contar)
    assert len(consultas) <= 8

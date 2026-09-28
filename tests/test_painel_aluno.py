"""
Área do aluno: cor e emoji por instrumento (pedido da direção de 22/09) e as três
telas pedidas pelo professor em 28/09 — Dashboard (pagamentos e inadimplência),
Minha Área (dados pessoais) e Observações pedagógicas (aulas do mês). Agregação
em app/indicadores.py.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import event

from app import db, visual_de_instrumento, INSTRUMENTO_VISUAL_PADRAO
from app.models import Aluno, Aula, Mensalidade, Pagamento, Usuario
from app.indicadores import (financeiro_do_aluno, pedagogico_do_aluno, proxima_aula,
                             rotulo_dia, situacao_presenca)

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


def ana_com_historico():
    ana = novo_aluno('Ana', 'Violino')
    ana.dia_aula_semana, ana.hora_aula = 2, time(14, 0)            # quarta, 14h
    aula(ana, date(2026, 9, 23), 'Presença: 1P/0A · Afinação melhorou.')      # a última
    aula(ana, date(2026, 9, 16), 'Presença: 0P/1A', 'Escala de Ré maior.')    # para estudar
    aula(ana, date(2026, 8, 10), 'Trabalhou o arco.', 'Estudo 12')            # sem marcação
    aula(ana, date(2026, 2, 5), 'Presença: 1P/0A')                 # no ano, fora dos 6 meses
    aula(ana, date(2025, 12, 1), 'Presença: 0P/1A', professora='Outra')       # ano passado
    mensalidade(ana, date(2025, 9, 10), 'pago', pago=250.0)        # fora da janela de 12 meses
    mensalidade(ana, date(2026, 8, 10), 'em atraso', pago=100.0)   # R$ 150 em aberto
    mensalidade(ana, date(2026, 9, 10), 'pago', pago=250.0)
    mensalidade(ana, date(2026, 11, 10), 'pendente')
    mensalidade(ana, date(2026, 10, 10), 'pendente', valor=260.0)  # a próxima
    db.session.commit()
    return db.session.get(Aluno, ana.id)


def test_pedagogico_do_aluno(app):
    with app.app_context():
        ana = ana_com_historico()
        p = pedagogico_do_aluno(ana, agora=AGORA)

        assert p['ano'] == 2026 and p['mes'] == '2026-09' and p['mes_rotulo'] == 'setembro de 2026'
        assert p['mes_anterior'] == '2026-08' and p['mes_seguinte'] is None
        assert [a['data'] for a in p['aulas_do_mes']] == [date(2026, 9, 23), date(2026, 9, 16)]
        assert p['aulas_do_mes'][0]['observacao'] == 'Afinação melhorou.'    # sem o marcador
        assert p['presenca_mes'] == {'aulas': 2, 'presencas': 1, 'faltas': 1, 'taxa': 0.5}
        assert p['presenca'] == {'aulas': 3, 'presencas': 2, 'faltas': 1, 'taxa': 2 / 3}
        assert p['proxima_aula'] == date(2026, 9, 30) and p['proxima_aula_rotulo'] == 'Qua, 30/09'
        assert p['professora'] == 'Flávia'
        assert p['ultima_aula']['data'] == date(2026, 9, 23)
        assert p['ultima_aula']['presenca'] == 'presente'
        estudar = p['para_estudar']
        assert estudar['data'] == date(2026, 9, 16)
        assert estudar['orientacao'] == 'Escala de Ré maior.' and estudar['observacao'] == ''

        meses = p['aulas_por_mes']
        assert meses['rotulos'] == ['abr/26', 'mai/26', 'jun/26', 'jul/26', 'ago/26', 'set/26']
        assert meses['presencas'] == [0, 0, 0, 0, 0, 1]
        assert meses['faltas'] == [0, 0, 0, 0, 0, 1]
        assert meses['sem_registro'] == [0, 0, 0, 0, 1, 0]
        assert meses['total'] == 3
        assert 'Total: 1 presença, 1 falta e 1 aula sem presença registrada.' in meses['descricao']

        agosto = pedagogico_do_aluno(ana, mes='2026-08', agora=AGORA)
        assert [a['orientacao'] for a in agosto['aulas_do_mes']] == ['Estudo 12']
        assert agosto['mes_seguinte'] == '2026-09' and agosto['presenca_mes']['taxa'] is None
        # Mês no futuro ou em formato errado cai no mês vigente.
        for mes in ('2026-10', '2026-13', 'setembro', None):
            assert pedagogico_do_aluno(ana, mes=mes, agora=AGORA)['mes'] == '2026-09'
        assert pedagogico_do_aluno(ana, mes='2026-01', agora=AGORA)['mes_anterior'] == '2025-12'


def test_financeiro_do_aluno(app):
    with app.app_context():
        ana = ana_com_historico()
        f = financeiro_do_aluno(ana, agora=AGORA)

        assert [(i['mensalidade'].competencia, i['falta']) for i in f['em_atraso']] == [('2026-08', 150.0)]
        assert f['valor_em_aberto'] == 150.0
        assert f['proxima'].vencimento == date(2026, 10, 10) and f['proxima'].valor == 260.0
        assert f['pago_no_ano'] == 350.0                          # 100 + 250, pelo vencimento
        assert (f['quitadas_no_ano'], f['do_ano']) == (1, 4)
        assert f['anos'] == [2026, 2025] and f['ano'] == 2026
        assert [m.competencia for m in f['lista']] == ['2026-11', '2026-10', '2026-09', '2026-08']

        g = f['por_mes']
        assert g['rotulos'][0] == 'out/25' and g['rotulos'][-1] == 'set/26' and len(g['rotulos']) == 12
        assert g['pago'][-2:] == [100.0, 250.0] and g['atraso'][-2:] == [150.0, 0.0]
        assert sum(g['a_vencer']) == 0                              # as pendentes vencem depois
        assert g['total'] == 500.0
        assert 'Total: R$ 350,00 pago, R$ 150,00 em atraso.' in g['descricao']

        assert [m.competencia for m in financeiro_do_aluno(ana, ano=2025, agora=AGORA)['lista']] == ['2025-09']
        assert financeiro_do_aluno(ana, ano=1999, agora=AGORA)['ano'] == 2026   # ano sem mensalidade


def test_area_do_aluno_sem_nada(app):
    with app.app_context():
        eva = novo_aluno('Eva', 'Canto')
        db.session.commit()
        p = pedagogico_do_aluno(db.session.get(Aluno, eva.id), agora=AGORA)
        assert p['presenca']['taxa'] is None and p['aulas_do_mes'] == []
        assert p['proxima_aula'] is None and p['proxima_aula_rotulo'] is None
        assert p['ultima_aula'] is None and p['para_estudar'] is None and p['professora'] is None
        assert p['aulas_por_mes']['total'] == 0
        f = financeiro_do_aluno(db.session.get(Aluno, eva.id), agora=AGORA)
        assert f['em_atraso'] == [] and f['proxima'] is None and f['lista'] == []
        assert f['por_mes']['total'] == 0 and f['anos'] == [] and f['ano'] == 2026


def conteudo(resposta):
    """Só o miolo da página: o <head> do base.html tem as classes CSS de risco."""
    return texto(resposta).split('class="main-content"', 1)[1]


def test_aluno_ve_as_tres_areas(dados, client):
    logar_aluno(client)
    html = conteudo(client.get('/dashboard'))                  # pagamentos
    assert 'Olá, Ana Silva' in html and 'Violão' in html and '🎸' in html
    assert 'Em dia' in html                    # 4 pagas + 1 pendente que vence em 20 dias
    assert 'mensalidadesChart' in html and 'Mensalidades em atraso' not in html
    assert 'aulasMesChart' not in html                         # as aulas foram para Observações

    html = conteudo(client.get('/observacoes'))                # aulas e o que estudar
    assert 'aulasMesChart' in html             # a aula de 5 dias atrás entra no gráfico
    dia_da_aula = (date.today() - timedelta(days=5)).strftime('%d/%m/%Y')
    assert f'Aula de {dia_da_aula} · Flávia' in html           # "para estudar agora"
    assert 'sem horário fixo' in html

    html = conteudo(client.get('/minha-area'))                 # dados pessoais
    assert 'Meus dados' in html and 'ana@aluno.com' in html and 'Minhas mensalidades' not in html

    for rota in ('/dashboard', '/observacoes', '/minha-area'):
        assert 'risco' not in conteudo(client.get(rota)).lower()   # o risco é da escola


def test_dashboard_mostra_atraso_e_exige_cadastro_vinculado(dados, app, client):
    with app.app_context():
        novo_usuario('Bruno Costa', 'bruno@aluno.com', Usuario.PAPEL_ALUNO)
        db.session.get(Aluno, dados['bruno']).email = 'bruno@aluno.com'
        novo_usuario('Sem Cadastro', 'sem@aluno.com', Usuario.PAPEL_ALUNO)
        db.session.commit()

    login(client, 'bruno@aluno.com')
    html = conteudo(client.get('/dashboard'))
    assert '2 em atraso' in html and 'R$ 600,00 em aberto' in html     # Piano, 2 x R$ 300
    assert 'Mensalidades em atraso' in html and '🎹' in html

    client.get('/logout')
    login(client, 'sem@aluno.com')
    for rota in ('/dashboard', '/observacoes'):
        r = client.get(rota)
        assert r.status_code == 302 and r.headers['Location'].endswith('/minha-area')
    assert 'Cadastro ainda não vinculado' in texto(client.get('/minha-area'))


def test_login_do_aluno_vai_para_o_dashboard(dados, client):
    r = logar_aluno(client)
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')


def test_minha_area_tem_a_cor_do_instrumento(dados, client):
    logar_aluno(client)
    html = conteudo(client.get('/minha-area'))
    violao = visual_de_instrumento('Violão')
    assert violao['emoji'] in html and violao['cor'] in html


def test_area_do_aluno_faz_poucas_consultas(dados, app, client):
    logar_aluno(client)
    with app.app_context():
        engine = db.engine
    for rota in ('/dashboard', '/observacoes', '/minha-area'):
        client.get(rota)                       # aquece a sessão e o cache de templates
        consultas = []

        def contar(*_):
            consultas.append(1)
        event.listen(engine, 'before_cursor_execute', contar)
        try:
            assert client.get(rota).status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', contar)
        assert len(consultas) <= 8, rota

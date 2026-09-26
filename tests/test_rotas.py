"""
Smoke das rotas por perfil + conteúdo das páginas gerenciais com o conjunto
``dados`` (ver conftest.py para os totais esperados).
"""
from datetime import date, timedelta

import pytest

from app import db
from app.models import Aluno, Mensalidade, Pagamento, Aula

from conftest import (logar_gestora, logar_professora, logar_aluno, texto,
                      novo_aluno, nova_mensalidade, competencia)


# ---------------------------------------------------------------------------
# Smoke por perfil
# ---------------------------------------------------------------------------
ROTAS_GESTORA = ['/dashboard', '/alunos', '/alunos?busca=ana', '/financeiro',
                 '/relatorios', '/acompanhamento', '/api/dashboard-data', '/registrar']
ROTAS_PROFESSORA_OK = ['/dashboard', '/alunos', '/acompanhamento', '/api/dashboard-data']
ROTAS_PROFESSORA_NEGADAS = ['/financeiro', '/relatorios', '/registrar']


@pytest.mark.parametrize('rota', ROTAS_GESTORA)
def test_gestora_acessa(dados, client, rota):
    logar_gestora(client)
    assert client.get(rota).status_code == 200


def test_gestora_ficha_do_aluno(dados, client):
    logar_gestora(client)
    r = client.get(f'/aluno/{dados["bruno"]}')
    assert r.status_code == 200 and 'Bruno Costa' in texto(r)
    assert client.get('/aluno/99999').status_code == 404


@pytest.mark.parametrize('rota', ROTAS_PROFESSORA_OK)
def test_professora_acessa(dados, client, rota):
    logar_professora(client)
    assert client.get(rota).status_code == 200


@pytest.mark.parametrize('rota', ROTAS_PROFESSORA_NEGADAS)
def test_professora_e_redirecionada(dados, client, rota):
    logar_professora(client)
    r = client.get(rota)
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')


def test_anonimo_vai_para_login(dados, client):
    for rota in ['/dashboard', '/alunos', '/financeiro', '/relatorios', '/acompanhamento',
                 f'/aluno/{dados["ana"]}', '/minha-area']:
        r = client.get(rota)
        assert r.status_code == 302 and '/login' in r.headers['Location'], rota
    assert client.get('/').status_code == 200
    assert client.get('/login').status_code == 200


def test_aluno_so_ve_a_propria_area(dados, client):
    logar_aluno(client)
    r = client.get('/dashboard')                  # painel do aluno (test_painel_aluno.py)
    assert r.status_code == 200 and 'Meu painel' in texto(r)
    r = client.get('/minha-area')
    assert r.status_code == 200 and 'Adimplente' in texto(r)
    # A ficha (com o risco de evasão) é da escola: o aluno vai para o painel,
    # seja a própria ficha, seja a de outro aluno.
    for ficha in (dados['ana'], dados['bruno'], 99999):
        r = client.get(f'/aluno/{ficha}')
        assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
    assert client.get('/alunos').status_code == 302
    assert client.get('/financeiro').status_code == 302


def test_logout(dados, client):
    logar_gestora(client)
    assert client.get('/logout').status_code == 302
    assert client.get('/dashboard').status_code == 302


def test_chartjs_carregado_uma_vez_so(dados, client):
    """Defeito nº 7: o dashboard pedia uma segunda cópia do Chart.js."""
    logar_gestora(client)
    assert texto(client.get('/dashboard')).count('npm/chart.js') == 1


# ---------------------------------------------------------------------------
# Conteúdo — dashboard, relatórios, financeiro, alunos
# ---------------------------------------------------------------------------
def test_dashboard_metricas(dados, client):
    logar_gestora(client)
    html = texto(client.get('/dashboard'))
    assert 'R$ 1.900,00' in html                 # recebido
    assert '/ 3.030,00' in html                  # previsto
    assert '<h2 class="text-primary">3</h2>' in html   # ativos
    assert '<h2 class="text-danger">2</h2>' in html    # inadimplentes
    assert '<h2 class="text-warning">3</h2>' in html   # mensalidades em atraso
    # alunos que pedem atenção: Bruno (alto) antes de Carla (médio); Ana e Diego fora
    assert html.index('Bruno Costa') < html.index('Carla Souza')
    assert 'Ana Silva' not in html.split('Alunos que pedem atenção')[1]
    assert 'Diego Lima' not in html
    assert '2 mensalidade(s) em atraso; sem acompanhamento há mais de 60 dias' in html
    assert '1 mensalidade(s) em atraso; nenhuma aula registrada' in html


def test_dashboard_graficos(dados, client):
    logar_gestora(client)
    html = texto(client.get('/dashboard'))
    assert '"baixo": 1' in html and '"m\\u00e9dio": 1' in html and '"alto": 1' in html
    # recebido por competência nos últimos 6 meses (do mais antigo ao atual):
    # m4 Ana 250 · m3 Ana+Bruno 550 · m2 Ana+Carla 530 · m1 Ana+Diego 570 · m0 nada
    meses = [competencia(i) for i in range(5, -1, -1)]
    assert f'"meses": {meses}'.replace("'", '"') in html
    assert '"valores": [0.0, 250.0, 550.0, 530.0, 570.0, 0.0]' in html
    assert 'data: [1]' in html and 'data: [2]' in html   # adimplentes / inadimplentes


def test_api_dashboard_data(dados, client):
    logar_gestora(client)
    r = client.get('/api/dashboard-data')
    dados_json = r.get_json()
    assert dados_json['risco'] == {'baixo': 1, 'médio': 1, 'alto': 1}
    assert dados_json['financeiro'] == {'Adimplentes': 1, 'Inadimplentes': 2}
    assert dados_json['instrumentos'] == {'Violão': 1, 'Piano': 1, 'Bateria': 1, 'Violino': 1}


def test_relatorios_totais(dados, client):
    logar_gestora(client)
    html = texto(client.get('/relatorios'))
    assert 'R$ 3.030,00' in html and 'R$ 1.900,00' in html and 'R$ 1.130,00' in html
    assert 'R$ 600,00' in html   # em aberto do Bruno
    assert 'R$ 280,00' in html   # em aberto da Carla
    assert 'Diego Lima' in html  # relatórios listam também os inativos
    assert html.count('Inadimplente<') == 2


def test_financeiro_lista_e_status(dados, client):
    logar_gestora(client)
    html = texto(client.get('/financeiro'))
    assert html.count('Em atraso</span>') == 3
    assert html.count('Pendente</span>') == 1
    assert html.count('Pago</span>') == 7
    assert html.count('Quitada') == 7


def test_alunos_lista_e_busca(dados, client):
    logar_gestora(client)
    html = texto(client.get('/alunos'))
    for nome in ['Ana Silva', 'Bruno Costa', 'Carla Souza', 'Diego Lima']:
        assert nome in html
    assert html.count('Inadimplente<') == 2
    html = texto(client.get('/alunos?busca=bru'))
    assert 'Bruno Costa' in html and 'Ana Silva' not in html


def test_get_recalcula_pendente_vencida_para_em_atraso(dados, app, client):
    """Uma mensalidade gravada como pendente cujo vencimento já passou (o tempo
    andou) tem de aparecer em atraso ao abrir as páginas."""
    with app.app_context():
        a = Aluno.query.filter_by(nome='Ana Silva').one()
        m = Mensalidade(aluno_id=a.id, competencia=competencia(1), valor=250.0,
                        vencimento=date.today() - timedelta(days=1), status='pendente')
        db.session.add(m)
        db.session.commit()
        m_id = m.id
    logar_gestora(client)
    html = texto(client.get('/dashboard'))
    assert '<h2 class="text-warning">4</h2>' in html
    assert '<h2 class="text-danger">3</h2>' in html   # Ana virou inadimplente
    with app.app_context():
        assert db.session.get(Mensalidade, m_id).status == 'em atraso'


# ---------------------------------------------------------------------------
# Escrita — mensalidade, pagamento, aluno, aula
# ---------------------------------------------------------------------------
def test_registrar_pagamento_quita(dados, app, client):
    with app.app_context():
        m = Mensalidade.query.filter_by(aluno_id=dados['bruno'], status='em atraso').first()
        m_id, valor = m.id, m.valor
    logar_gestora(client)
    r = client.post(f'/registrar_pagamento/{m_id}', data={'data_pagamento': date.today().isoformat()})
    assert r.status_code == 302
    with app.app_context():
        m = db.session.get(Mensalidade, m_id)
        assert m.status == 'pago' and m.valor_pago == valor
        assert len(m.pagamentos) == 1


def test_registrar_pagamento_parcial(dados, app, client):
    with app.app_context():
        m = Mensalidade.query.filter_by(aluno_id=dados['carla'], status='em atraso').first()
        m_id = m.id
    logar_gestora(client)
    client.post(f'/registrar_pagamento/{m_id}', data={'valor': '80'})
    with app.app_context():
        m = db.session.get(Mensalidade, m_id)
        assert m.status == 'em atraso' and m.valor_pago == 80.0
    html = texto(client.get('/relatorios'))
    assert 'R$ 200,00' in html   # Carla: 280 - 80


def test_pagamento_em_partes_quita_sem_erro_de_centavo(dados, app, client):
    """Defeito nº 4 (dinheiro em float): 100,10 + 150,20 = 250,2999… e a
    mensalidade de 250,30 continuava em atraso. A comparação é em centavos e o
    saldo padrão vai arredondado."""
    with app.app_context():
        m = Mensalidade.query.filter_by(aluno_id=dados['carla'], status='em atraso').first()
        m.valor = 250.30
        db.session.commit()
        m_id = m.id
    logar_gestora(client)
    client.post(f'/registrar_pagamento/{m_id}', data={'valor': '100.10'})
    client.post(f'/registrar_pagamento/{m_id}', data={'valor': '150.20'})
    with app.app_context():
        assert db.session.get(Mensalidade, m_id).status == 'pago'

    with app.app_context():
        m = Mensalidade.query.filter_by(aluno_id=dados['bruno'], status='em atraso').first()
        m.valor = 250.30
        m.pagamentos.append(Pagamento(valor=100.10, data_pagamento=date.today()))
        db.session.commit()
        m_id = m.id
    html = texto(client.get('/financeiro?status=em+atraso'))
    assert 'name="valor" value="150.20"' in html          # saldo em centavos, sem 150.2000…
    client.post(f'/registrar_pagamento/{m_id}', data={})  # sem valor: paga o saldo
    with app.app_context():
        m = db.session.get(Mensalidade, m_id)
        assert m.status == 'pago' and m.pagamentos[-1].valor == 150.20


def test_adicionar_mensalidade(dados, app, client):
    logar_gestora(client)
    r = client.post('/adicionar_mensalidade', data={
        'aluno_id': dados['ana'], 'competencia': '2030-01', 'valor': '99.9',
        'vencimento': '2030-01-10'})
    assert r.status_code == 302
    with app.app_context():
        m = Mensalidade.query.filter_by(competencia='2030-01').one()
        assert m.status == 'pendente' and m.valor == 99.9


def test_adicionar_editar_excluir_aluno(dados, app, client):
    logar_gestora(client)
    r = client.post('/adicionar_aluno', data={'nome': 'Novo Aluno', 'instrumento_id': 1,
                                              'mensalidade_base': '150'})
    assert r.status_code == 302 and '/aluno/' in r.headers['Location']
    with app.app_context():
        a = Aluno.query.filter_by(nome='Novo Aluno').one()
        a_id = a.id
    r = client.post(f'/editar_aluno/{a_id}', data={'nome': 'Aluno Editado', 'instrumento_id': 2,
                                                   'status': 'inativo'})
    assert r.status_code == 302
    with app.app_context():
        a = db.session.get(Aluno, a_id)
        assert a.nome == 'Aluno Editado' and a.status == 'inativo' and a.instrumento_id == 2
    client.post(f'/excluir_aluno/{a_id}')
    with app.app_context():
        assert db.session.get(Aluno, a_id) is None


def test_acompanhamento_registra_aula(dados, app, client):
    """A professora registra aula para um aluno dela (Ana já teve aula com ela)."""
    logar_professora(client)
    r = client.post('/acompanhamento', data={'aluno_id': dados['ana'],
                                             'observacao': 'Primeira aula', 'orientacao_estudo': 'Escalas'})
    assert r.status_code == 302
    with app.app_context():
        aula = (Aula.query.filter_by(aluno_id=dados['ana'])
                .order_by(Aula.id.desc()).first())
        assert aula.professora == 'Flávia (Professora)' and aula.data == date.today()
        assert aula.observacao == 'Primeira aula'
    html = texto(client.get('/acompanhamento'))
    assert 'Primeira aula' in html


def test_acompanhamento_nao_registra_aula_de_aluno_de_outra(dados, app, client):
    """Carla não teve aula com a professora logada: não é aluna dela, e tentar
    registrar por ela (ou só adivinhar o id) é barrado — senão bastaria um POST
    para passar a enxergar a ficha de qualquer aluno da escola."""
    logar_professora(client)
    r = client.post('/acompanhamento', data={'aluno_id': dados['carla'],
                                             'observacao': 'Aula de outra'})
    assert r.status_code == 403
    with app.app_context():
        assert Aula.query.filter_by(aluno_id=dados['carla']).count() == 0


def test_dashboard_pagina_os_alunos_que_pedem_atencao(app):
    """Mais de 10 alunos em atenção: as linhas saem numeradas por página
    (data-pagina) e o rodapé de paginação aparece; até 10, não."""
    from conftest import novo_usuario, novo_aluno, nova_mensalidade
    from app.models import Usuario
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        for i in range(12):                              # 1 vencida cada -> risco médio (25)
            nova_mensalidade(novo_aluno(f'Aluno {i:02d}'), 1, False)
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    html = texto(client.get('/dashboard'))
    assert html.count('data-pagina="0"') == 10 and html.count('data-pagina="1"') == 2
    assert 'id="paginacaoAtencao"' in html and 'data-por-pagina="10"' in html

    with app.app_context():
        for a in Aluno.query.order_by(Aluno.nome.desc()).limit(2).all():
            a.status = 'inativo'                         # sobram 10 em atenção
        db.session.commit()
    html = texto(client.get('/dashboard'))
    assert html.count('data-pagina="0"') == 10 and 'data-pagina="1"' not in html
    assert 'id="paginacaoAtencao"' not in html

"""
Perfil professora: ela vê os alunos dela e nada do financeiro da escola.

Pedido da direção em 22/09/2026 — o painel expunha entradas, inadimplência e
atrasos, e a aba Alunos listava a escola inteira. O vínculo professora↔aluno é o
que o modelo tem hoje: as aulas que ela registrou (``Aula.professora`` casado com
o nome do usuário; ver ``routes.ids_alunos_da_professora``).

No conjunto ``dados``: Ana e Bruno tiveram aula com a professora Flávia; Carla e
Diego, não.
"""
from app import db
from app.models import Aula

from conftest import logar_gestora, logar_professora, texto


# ---------------------------------------------------------------------------
# Vínculo
# ---------------------------------------------------------------------------
def test_nome_de_professora_ignora_rotulo_entre_parenteses():
    from app.routes import nome_de_professora
    assert nome_de_professora('Flávia (Professora)') == 'flávia'
    assert nome_de_professora('  FLÁVIA  ') == 'flávia'
    assert nome_de_professora('Docente 7') == 'docente 7'
    assert nome_de_professora(None) == ''


def test_ids_alunos_da_professora(dados, app):
    from app.routes import ids_alunos_da_professora
    from app.models import Usuario
    with app.app_context():
        flavia = Usuario.query.filter_by(email='professora@escola.com').one()
        ids = set(ids_alunos_da_professora(flavia))
    assert ids == {dados['ana'], dados['bruno']}


# ---------------------------------------------------------------------------
# Lista de alunos
# ---------------------------------------------------------------------------
def test_alunos_lista_somente_os_da_professora(dados, client):
    logar_professora(client)
    html = texto(client.get('/alunos'))
    assert 'Ana Silva' in html and 'Bruno Costa' in html
    assert 'Carla Souza' not in html and 'Diego Lima' not in html


def test_alunos_nao_mostra_financeiro_para_a_professora(dados, client):
    logar_professora(client)
    html = texto(client.get('/alunos'))
    assert 'Inadimplente' not in html and 'Mensalidade' not in html
    assert 'Risco' in html                       # o risco continua visível

    client.get('/logout')
    logar_gestora(client)
    html = texto(client.get('/alunos'))
    assert 'Inadimplente' in html and 'Mensalidade' in html


def test_busca_da_professora_nao_alcanca_aluno_de_outra(dados, client):
    logar_professora(client)
    assert 'Carla Souza' not in texto(client.get('/alunos?busca=carla'))


# ---------------------------------------------------------------------------
# Ficha do aluno
# ---------------------------------------------------------------------------
def test_ficha_de_aluno_de_outra_professora_e_proibida(dados, client):
    logar_professora(client)
    assert client.get(f"/aluno/{dados['carla']}").status_code == 403
    assert client.get(f"/aluno/{dados['ana']}").status_code == 200


def test_ficha_sem_bloco_financeiro_para_a_professora(dados, client):
    logar_professora(client)
    html = texto(client.get(f"/aluno/{dados['ana']}"))
    assert 'Histórico de mensalidades' not in html
    assert 'Valor em aberto' not in html
    assert 'Fatores de risco' not in html
    assert 'Risco de evasão' in html             # o nível fica

    client.get('/logout')
    logar_gestora(client)
    html = texto(client.get(f"/aluno/{dados['ana']}"))
    assert 'Histórico de mensalidades' in html and 'Valor em aberto' in html


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------
def test_dashboard_da_professora_nao_tem_financeiro(dados, client):
    logar_professora(client)
    html = texto(client.get('/dashboard'))
    for proibido in ['Inadimplentes', 'Mensalidades em atraso', 'Recebido / previsto',
                     'Situação financeira', 'Recebido - últimos 6 meses',
                     'Alunos que pedem atenção']:
        assert proibido not in html, proibido
    for esperado in ['Meus alunos ativos', 'Alunos por instrumento', 'Risco de evasão',
                     'Presença por instrumento', 'Alunos com mais faltas',
                     'Aulas por dia da semana', 'Ocupação de horários']:
        assert esperado in html, esperado


def test_dashboard_da_professora_conta_so_os_alunos_dela(dados, client):
    """Ana e Bruno são ativos e dela; Carla é ativa mas não é dela."""
    logar_professora(client)
    html = texto(client.get('/dashboard'))
    assert '<h2 class="text-primary">2</h2>' in html
    # os dados dos gráficos saem em JSON com acento escapado (tojson)
    assert 'Piano' in html and r'Viol\u00e3o' in html  # instrumentos de Ana e Bruno
    assert 'Bateria' not in html                       # instrumento da Carla


def test_dashboard_da_gestora_continua_completo(dados, client):
    logar_gestora(client)
    html = texto(client.get('/dashboard'))
    for esperado in ['Inadimplentes', 'Mensalidades em atraso', 'Recebido / previsto',
                     'Situação financeira', 'Alunos que pedem atenção']:
        assert esperado in html, esperado


def test_api_dashboard_data_sem_financeiro_para_a_professora(dados, client):
    logar_professora(client)
    dados_json = client.get('/api/dashboard-data').get_json()
    assert 'financeiro' not in dados_json
    assert sum(dados_json['risco'].values()) == 2          # só Ana e Bruno
    assert set(dados_json['instrumentos']) == {'Violão', 'Piano'}

    client.get('/logout')
    logar_gestora(client)
    assert 'financeiro' in client.get('/api/dashboard-data').get_json()


# ---------------------------------------------------------------------------
# Acompanhamento
# ---------------------------------------------------------------------------
def test_acompanhamento_lista_so_as_aulas_dos_alunos_dela(dados, app, client):
    with app.app_context():                       # aula de Carla, de outra professora
        db.session.add(Aula(aluno_id=dados['carla'], professora='Outra Docente',
                            observacao='Aula da Carla'))
        db.session.commit()
    logar_professora(client)
    html = texto(client.get('/acompanhamento'))
    assert 'Ana Silva' in html
    assert 'Aula da Carla' not in html and 'Carla Souza' not in html

    client.get('/logout')
    logar_gestora(client)
    assert 'Aula da Carla' in texto(client.get('/acompanhamento'))


# ---------------------------------------------------------------------------
# Edição do acompanhamento (o "estudos da semana")
# ---------------------------------------------------------------------------
def test_professora_edita_a_observacao_da_aula(dados, app, client):
    with app.app_context():
        aula_id = Aula.query.filter_by(aluno_id=dados['ana']).one().id
    logar_professora(client)
    r = client.post(f'/aula/{aula_id}/editar',
                    data={'observacao': 'Revisão de escalas', 'presenca': 'presente',
                          'orientacao_estudo': 'Estudar peça nova'})
    assert r.status_code == 302
    with app.app_context():
        aula = db.session.get(Aula, aula_id)
        assert aula.observacao == 'Presença: 1P/0A · Revisão de escalas'
        assert aula.orientacao_estudo == 'Estudar peça nova'


def test_editar_aula_nao_empilha_o_marcador_de_presenca(dados, app, client):
    """Editar duas vezes não pode gerar 'Presença: ... · Presença: ...'."""
    with app.app_context():
        aula_id = Aula.query.filter_by(aluno_id=dados['ana']).one().id
    logar_professora(client)
    for texto_obs in ['Primeira versão', 'Segunda versão']:
        client.post(f'/aula/{aula_id}/editar', data={'observacao': texto_obs,
                                                     'presenca': 'falta'})
    with app.app_context():
        assert db.session.get(Aula, aula_id).observacao == 'Presença: 0P/1A · Segunda versão'


def test_editar_aula_sem_escolher_presenca_preserva_a_marcacao(dados, app, client):
    with app.app_context():
        aula_id = Aula.query.filter_by(aluno_id=dados['ana']).one().id
        db.session.get(Aula, aula_id).observacao = 'Presença: 0P/1A · Faltou'
        db.session.commit()
    logar_professora(client)
    client.post(f'/aula/{aula_id}/editar', data={'observacao': 'Faltou, avisou depois'})
    with app.app_context():
        assert db.session.get(Aula, aula_id).observacao == 'Presença: 0P/1A · Faltou, avisou depois'


def test_editar_aula_com_presenca_vazia_remove_a_marcacao(dados, app, client):
    """"Não registrar" no formulário apaga o marcador — é escolha explícita,
    diferente de não mandar o campo."""
    with app.app_context():
        aula_id = Aula.query.filter_by(aluno_id=dados['ana']).one().id
        db.session.get(Aula, aula_id).observacao = 'Presença: 1P/0A · Veio'
        db.session.commit()
    logar_professora(client)
    client.post(f'/aula/{aula_id}/editar', data={'observacao': 'Veio', 'presenca': ''})
    with app.app_context():
        assert db.session.get(Aula, aula_id).observacao == 'Veio'


def test_professora_nao_edita_aula_de_aluno_de_outra(dados, app, client):
    with app.app_context():
        aula = Aula(aluno_id=dados['carla'], professora='Outra Docente',
                    observacao='Aula da Carla')
        db.session.add(aula)
        db.session.commit()
        aula_id = aula.id
    logar_professora(client)
    assert client.post(f'/aula/{aula_id}/editar',
                       data={'observacao': 'invadindo'}).status_code == 403
    with app.app_context():
        assert db.session.get(Aula, aula_id).observacao == 'Aula da Carla'

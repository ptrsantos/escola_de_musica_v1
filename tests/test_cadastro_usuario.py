"""
Cadastro de usuário com a ficha de aluno (pedido da direção, 28/09/2026): para o
perfil aluno, o login e a ficha (instrumento, nascimento, endereço, mensalidade)
nascem juntos; perfis nos dois gêneros; login de aluno sem ficha pode ser
completado pela tela Usuários.
"""
from datetime import date

from bs4 import BeautifulSoup

from app import db
from app.models import Aluno, Instrumento, Usuario

from conftest import SENHA, logar_gestora, login, novo_aluno, novo_usuario, texto


def gestora_logada(app, client):
    with app.app_context():
        novo_usuario('Marina', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        db.session.commit()
    logar_gestora(client)


def id_instrumento(app, nome):
    with app.app_context():
        return Instrumento.query.filter_by(nome=nome).one().id


def ficha(app, email):
    with app.app_context():
        a = Aluno.query.filter_by(email=email).one_or_none()
        return None if a is None else {
            'nome': a.nome, 'instrumento': a.instrumento.nome, 'nascimento': a.data_nascimento,
            'endereco': a.endereco, 'mensalidade': a.mensalidade_base, 'status': a.status}


def contar(app, modelo):
    with app.app_context():
        return modelo.query.count()


def aluno_novo(app, **extra):
    return {'nome': 'Roberta Fernandez', 'email': 'Roberta@Escola.com', 'senha': SENHA,
            'papel': 'aluno', 'instrumento_id': id_instrumento(app, 'Teclado'),
            'data_nascimento': '2012-10-03', 'endereco': 'Rua A, 10',
            'mensalidade_base': '250,00', **extra}


def test_cadastro_de_aluno_cria_o_login_e_a_ficha(app, client):
    gestora_logada(app, client)
    r = client.post('/registrar', data=aluno_novo(app))
    assert r.status_code == 302 and r.headers['Location'].endswith('/registrar')
    assert ficha(app, 'roberta@escola.com') == {
        'nome': 'Roberta Fernandez', 'instrumento': 'Teclado', 'nascimento': date(2012, 10, 3),
        'endereco': 'Rua A, 10', 'mensalidade': 250.0, 'status': 'ativo'}
    msg = texto(client.get('/registrar'))
    assert 'cadastrado como Aluno / Aluna, com a ficha de aluno' in msg

    client.get('/logout')
    assert login(client, 'roberta@escola.com').status_code == 302
    html = texto(client.get('/minha-area'))
    assert 'Rua A, 10' in html and '03/10/2012' in html and 'Teclado' in html


def test_aluno_sem_instrumento_nao_e_criado(app, client):
    gestora_logada(app, client)
    r = client.post('/registrar', data=aluno_novo(app, instrumento_id=''))
    assert r.status_code == 200
    arvore = BeautifulSoup(texto(r), 'html.parser')
    campo = arvore.find(id='instrumento_id')
    assert campo.get('aria-invalid') == 'true'
    assert arvore.select_one('.alert[role="alert"] a[href="#instrumento_id"]') is not None
    assert arvore.find(id='nome').get('value') == 'Roberta Fernandez'   # valores preservados
    assert contar(app, Usuario) == 1 and contar(app, Aluno) == 0


def test_ficha_existente_e_vinculada_sem_duplicar(app, client):
    gestora_logada(app, client)
    with app.app_context():
        novo_aluno('Roberta F.', 'Piano', email='roberta@escola.com')
        db.session.commit()
    r = client.post('/registrar', data=aluno_novo(app, instrumento_id=''))  # não exigido
    assert r.status_code == 302
    assert contar(app, Aluno) == 1
    assert ficha(app, 'roberta@escola.com')['instrumento'] == 'Piano'      # nada sobrescrito
    assert 'vinculado à ficha de aluno de Roberta F.' in texto(client.get('/registrar'))


def test_professora_nao_ganha_ficha(app, client):
    gestora_logada(app, client)
    r = client.post('/registrar', data=aluno_novo(app, papel='professora', email='prof@escola.com'))
    assert r.status_code == 302
    assert contar(app, Aluno) == 0
    assert 'cadastrado como Professor / Professora.' in texto(client.get('/registrar'))


def test_modal_novo_usuario_tambem_cria_a_ficha(app, client):
    gestora_logada(app, client)
    r = client.post('/adicionar_usuario', data=aluno_novo(app))
    assert r.status_code == 302
    assert ficha(app, 'roberta@escola.com')['instrumento'] == 'Teclado'

    r = client.post('/adicionar_usuario', data=aluno_novo(app, email='outro@escola.com',
                                                           instrumento_id=''))
    arvore = BeautifulSoup(texto(r), 'html.parser')
    assert arvore.find(id='novo-u-instrumento_id').get('aria-invalid') == 'true'
    # O resumo aponta para os ids do modal (novo-u-*), não para campo-*.
    assert arvore.select_one('#novo-usuario-resumo-erros a[href="#novo-u-instrumento_id"]')


def test_perfis_nos_dois_generos(app, client):
    gestora_logada(app, client)
    arvore = BeautifulSoup(texto(client.get('/registrar')), 'html.parser')
    opcoes = [o.get_text() for o in arvore.find(id='papel').find_all('option')]
    assert opcoes == ['Aluno / Aluna', 'Professor / Professora', 'Gestor / Gestora']
    html = texto(client.get('/usuarios'))
    assert 'Gestor / Gestora' in html and 'Marina · Gestor / Gestora' in html


def test_login_de_aluno_sem_ficha_pode_ser_completado(app, client):
    gestora_logada(app, client)
    with app.app_context():
        sem = novo_usuario('Sem Ficha', 'sem@escola.com', Usuario.PAPEL_ALUNO).id
        novo_usuario('Com Ficha', 'com@escola.com', Usuario.PAPEL_ALUNO)
        novo_aluno('Com Ficha', email='com@escola.com')
        db.session.commit()
    arvore = BeautifulSoup(texto(client.get('/usuarios')), 'html.parser')
    links = [a['href'] for a in arvore.find_all('a') if 'Completar a ficha' in a.get_text()]
    assert links == [f'/alunos?completar_usuario={sem}']

    arvore = BeautifulSoup(texto(client.get(links[0])), 'html.parser')
    assert arvore.body.get('data-abrir-modal') == 'novoAlunoModal'
    assert arvore.find(id='campo-nome').get('value') == 'Sem Ficha'
    assert arvore.find(id='campo-email').get('value') == 'sem@escola.com'


def test_trocar_o_email_do_aluno_leva_a_ficha_junto(app, client):
    gestora_logada(app, client)
    with app.app_context():
        uid = novo_usuario('Ana', 'ana@aluno.com', Usuario.PAPEL_ALUNO).id
        novo_aluno('Ana', email='ana@aluno.com')
        db.session.commit()
    r = client.post(f'/editar_usuario/{uid}', data={'nome': 'Ana', 'email': 'ana.nova@aluno.com',
                                                   'papel': 'aluno', 'senha': ''})
    assert r.status_code == 302
    assert ficha(app, 'ana.nova@aluno.com') is not None and ficha(app, 'ana@aluno.com') is None

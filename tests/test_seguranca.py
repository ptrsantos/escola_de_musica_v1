"""
Segurança — recria os testes de 10/09 (fechamento do /registrar e CSRF).
"""
from app import db
from app.models import Usuario, Aluno

from conftest import (criar_app, novo_usuario, novo_aluno, logar_gestora, logar_professora,
                      obter_csrf, texto, SENHA)


def cadastro(nome='Novo', email='novo@escola.com', papel=None, senha=SENHA):
    dados = {'nome': nome, 'email': email, 'senha': senha}
    if papel is not None:
        dados['papel'] = papel
    return dados


def contar_usuarios(app):
    with app.app_context():
        return Usuario.query.count()


def papel_de(app, email):
    with app.app_context():
        return Usuario.query.filter_by(email=email).one().papel


# ---------------------------------------------------------------------------
# /registrar
# ---------------------------------------------------------------------------
def test_bootstrap_primeira_conta_vira_gestora_mesmo_pedindo_outro_papel(app, client):
    assert contar_usuarios(app) == 0
    assert client.get('/registrar').status_code == 200

    r = client.post('/registrar', data=cadastro(papel='aluno'))
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert contar_usuarios(app) == 1
    assert papel_de(app, 'novo@escola.com') == Usuario.PAPEL_GESTORA


def test_visitante_nao_cria_conta_depois_do_bootstrap(app, client):
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        db.session.commit()

    r = client.get('/registrar')
    assert r.status_code == 302 and '/login' in r.headers['Location']

    r = client.post('/registrar', data=cadastro(papel='gestora'))
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert contar_usuarios(app) == 1


def test_professora_logada_nao_cadastra(app, client):
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        novo_usuario('Prof', 'professora@escola.com', Usuario.PAPEL_PROFESSORA)
        db.session.commit()
    logar_professora(client)

    r = client.post('/registrar', data=cadastro(papel='gestora'))
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
    assert contar_usuarios(app) == 2


def test_gestora_logada_cadastra_com_o_papel_escolhido(app, client):
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        db.session.commit()
    logar_gestora(client)

    assert client.get('/registrar').status_code == 200
    r = client.post('/registrar', data=cadastro(email='prof@escola.com', papel='professora'))
    assert r.status_code == 302
    assert papel_de(app, 'prof@escola.com') == Usuario.PAPEL_PROFESSORA


def test_papel_invalido_cai_em_aluno(app, client):
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        db.session.commit()
    logar_gestora(client)

    client.post('/registrar', data=cadastro(email='x@escola.com', papel='superadmin'))
    assert papel_de(app, 'x@escola.com') == Usuario.PAPEL_ALUNO

    client.post('/registrar', data=cadastro(email='y@escola.com'))   # sem campo papel
    assert papel_de(app, 'y@escola.com') == Usuario.PAPEL_ALUNO


def test_validacoes_basicas_do_cadastro(app, client):
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        db.session.commit()
    logar_gestora(client)

    r = client.post('/registrar', data=cadastro(senha='123'))          # senha curta
    assert r.status_code == 200 and 'pelo menos 6' in texto(r)
    r = client.post('/registrar', data=cadastro(email='gestora@escola.com'))  # e-mail repetido
    assert r.status_code == 200 and 'Já existe' in texto(r)
    assert contar_usuarios(app) == 1


# ---------------------------------------------------------------------------
# CSRF — aplicação com a proteção ligada
# ---------------------------------------------------------------------------
def test_csrf_barra_post_sem_token():
    app = criar_app(WTF_CSRF_ENABLED=True)
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        aluno = novo_aluno('Aluno Teste')
        db.session.commit()
        aluno_id = aluno.id
    client = app.test_client()

    # login sem token: redirect com aviso, e o usuário NÃO fica logado
    r = client.post('/login', data={'email': 'gestora@escola.com', 'senha': SENHA})
    assert r.status_code == 302
    assert client.get('/dashboard').status_code == 302   # ainda anônimo

    # login com token válido funciona
    token = obter_csrf(client, '/login')
    r = client.post('/login', data={'email': 'gestora@escola.com', 'senha': SENHA,
                                    'csrf_token': token})
    assert r.status_code == 302 and r.headers['Location'].endswith('/inicio')
    assert client.get('/dashboard').status_code == 200

    # exclusão sem token é barrada
    r = client.post(f'/excluir_aluno/{aluno_id}')
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Aluno, aluno_id) is not None

    # com token, exclui
    token = obter_csrf(client, '/alunos')
    client.post(f'/excluir_aluno/{aluno_id}', data={'csrf_token': token})
    with app.app_context():
        assert db.session.get(Aluno, aluno_id) is None
        db.session.remove()
        db.drop_all()


def test_todo_formulario_post_tem_token_csrf():
    """Com CSRFProtect ativo, um formulário POST sem csrf_token nunca salva: o
    usuário só vê "sua sessão expirou". Aconteceu com as telas que chegaram na
    main em 22/09 (instrumentos, usuários, financeiro), então virou teste."""
    import os
    import re
    raiz = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'app', 'templates')
    abertura = re.compile(r'<form[^>]*method="POST"[^>]*>', re.IGNORECASE)
    sem_token = []
    for nome in sorted(os.listdir(raiz)):
        if not nome.endswith('.html'):
            continue
        with open(os.path.join(raiz, nome), encoding='utf-8') as f:
            conteudo = f.read()
        for m in abertura.finditer(conteudo):
            fim = conteudo.find('</form>', m.end())
            corpo = conteudo[m.end():fim if fim != -1 else len(conteudo)]
            if 'csrf_token()' not in corpo:
                linha = conteudo[:m.start()].count(chr(10)) + 1
                sem_token.append(f'{nome}:{linha}')
    assert not sem_token, 'formulários POST sem csrf_token: ' + ', '.join(sem_token)

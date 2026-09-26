"""
Troca de senha a partir da tela de login (/alterar-senha): exige a senha
atual, não revela se o e-mail existe e valida a nova senha.
"""
import pytest

from app import db
from app.models import Usuario

from conftest import SENHA, login, logar_aluno, texto

EMAIL = 'professora@escola.com'


def trocar(client, email=EMAIL, atual=SENHA, nova='nova-senha', confirmacao=None):
    return client.post('/alterar-senha', data={
        'email': email, 'senha_atual': atual, 'nova_senha': nova,
        'confirmar_senha': nova if confirmacao is None else confirmacao})


def senha_confere(app, senha, email=EMAIL):
    with app.app_context():
        return Usuario.query.filter_by(email=email).one().verificar_senha(senha)


def test_login_tem_o_link_e_a_tela_abre_sem_login(client):
    assert 'href="/alterar-senha"' in texto(client.get('/login'))
    r = client.get('/alterar-senha')
    assert r.status_code == 200 and 'Senha atual' in texto(r)


def test_troca_a_senha(dados, app, client):
    r = trocar(client, email='  Professora@Escola.com ')   # e-mail como no login: sem espaço e caixa
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert senha_confere(app, 'nova-senha') and not senha_confere(app, SENHA)
    r = login(client, EMAIL, SENHA)                            # a senha antiga não entra
    assert r.status_code == 200 and 'Email ou senha inválidos.' in texto(r)
    r = login(client, EMAIL, 'nova-senha')
    assert r.status_code == 302 and r.headers['Location'].endswith('/inicio')


@pytest.mark.parametrize('email, atual', [(EMAIL, 'errada'), ('ninguem@escola.com', SENHA)])
def test_senha_errada_e_email_inexistente_dao_a_mesma_mensagem(dados, app, client, email, atual):
    r = trocar(client, email=email, atual=atual)
    assert r.status_code == 200 and 'E-mail ou senha atual inválidos.' in texto(r)
    assert senha_confere(app, SENHA)


@pytest.mark.parametrize('nova, confirmacao, mensagem', [
    ('12345', '12345', 'pelo menos 6 caracteres'),
    ('nova-senha', 'outra-senha', 'A confirmação não confere'),
    (SENHA, SENHA, 'diferente da atual'),
    ('', '', 'Preencha todos os campos'),
])
def test_nova_senha_invalida_nao_troca(dados, app, client, nova, confirmacao, mensagem):
    r = trocar(client, nova=nova, confirmacao=confirmacao)
    assert r.status_code == 200 and mensagem in texto(r)
    assert senha_confere(app, SENHA)


def test_logado_tem_o_email_preenchido_e_sai_ao_trocar(dados, app, client):
    logar_aluno(client)
    assert 'value="ana@aluno.com"' in texto(client.get('/alterar-senha'))
    trocar(client, email='ana@aluno.com')
    assert senha_confere(app, 'nova-senha', email='ana@aluno.com')
    r = client.get('/dashboard')                  # a sessão antiga não vale mais
    assert r.status_code == 302 and '/login' in r.headers['Location']

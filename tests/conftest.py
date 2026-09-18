"""
Fixtures compartilhadas. Cada teste recebe uma aplicação nova apontando para um
SQLite em memória — nada toca em instance/escola_musica.db.

Os dados de setup são criados dentro de ``with app.app_context()`` e as
requisições vão pelo test client, que abre o próprio contexto (e a própria
sessão do SQLAlchemy), como em produção.
"""
import os
import re
import sys
from datetime import date, timedelta

import pytest

# raiz do projeto (pasta que contém app/, importar_mysql.py, ...)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import (Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula,  # noqa: E402
                        inicializar_instrumentos)

SENHA = '123456'
CONFIG_TESTE = {
    'TESTING': True,
    'SQLALCHEMY_DATABASE_URI': 'sqlite://',   # em memória, uma base por aplicação
    'WTF_CSRF_ENABLED': False,
    'SECRET_KEY': 'chave-de-teste',
    'MODELO_EVASAO': None,   # sem modelo: o risco é a regra RISK_CONFIG (test_risco.py liga o modelo)
}


def competencia(meses_atras, hoje=None):
    """'YYYY-MM' de N meses atrás, contando meses de verdade (não 30 dias)."""
    hoje = hoje or date.today()
    ano, mes = divmod(hoje.year * 12 + hoje.month - 1 - meses_atras, 12)
    return f'{ano:04d}-{mes + 1:02d}'


def criar_app(**extras):
    app = create_app({**CONFIG_TESTE, **extras})
    with app.app_context():
        db.create_all()
        inicializar_instrumentos()
    return app


@pytest.fixture(autouse=True)
def _hash_rapido(monkeypatch):
    """O scrypt padrão leva ~0,2 s por usuário; nos testes basta um hash barato."""
    import app.models as modelos
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(modelos, 'generate_password_hash',
                        lambda senha: generate_password_hash(senha, method='pbkdf2:sha256:1000'))


@pytest.fixture
def app():
    app = criar_app()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers de dados
# ---------------------------------------------------------------------------
def novo_usuario(nome, email, papel, senha=SENHA):
    u = Usuario(nome=nome, email=email, papel=papel)
    u.set_senha(senha)
    db.session.add(u)
    db.session.flush()
    return u


def novo_aluno(nome, instrumento='Violão', mensalidade=250.0, status='ativo', email=None):
    inst = Instrumento.query.filter_by(nome=instrumento).one()
    a = Aluno(nome=nome, instrumento_id=inst.id, mensalidade_base=mensalidade,
              status=status, email=email)
    db.session.add(a)
    db.session.flush()
    return a


def nova_mensalidade(aluno, meses_atras, pago, valor=None, vencimento=None):
    """Mensalidade de N meses atrás; ``pago=True`` gera um Pagamento integral.
    O status é calculado como o seed e o importador fazem."""
    valor = aluno.mensalidade_base if valor is None else valor
    if vencimento is None:
        ano, mes = competencia(meses_atras).split('-')
        vencimento = date(int(ano), int(mes), 10)
    m = Mensalidade(aluno_id=aluno.id, competencia=competencia(meses_atras),
                    valor=valor, vencimento=vencimento)
    if pago:
        m.pagamentos.append(Pagamento(valor=valor, data_pagamento=vencimento))
    m.atualizar_status()
    db.session.add(m)
    db.session.flush()
    return m


def nova_aula(aluno, dias_atras, professora='Flávia'):
    a = Aula(aluno_id=aluno.id, professora=professora,
             data=date.today() - timedelta(days=dias_atras),
             observacao='obs', orientacao_estudo='estudar')
    db.session.add(a)
    db.session.flush()
    return a


def popular_dados(app):
    """Conjunto pequeno e determinístico, no espírito do popular_escola.py.

    Ana    ativa, 4 pagas + 1 pendente (vence no futuro), aula há 5 dias  → adimplente, baixo
    Bruno  ativo, 1 paga + 2 vencidas sem pagamento, aula há 95 dias      → inadimplente, alto (65)
    Carla  ativa, 1 paga + 1 vencida, sem aula                            → inadimplente, médio (40)
    Diego  inativo, 1 paga                                                → fora do dashboard

    Totais: previsto 3030,00 · recebido 1900,00 · em aberto 1130,00 ·
    3 mensalidades em atraso · 3 ativos, 2 inadimplentes.
    (Scores acima são da regra; test_risco.py usa o mesmo conjunto com o modelo.)
    """
    with app.app_context():
        novo_usuario('Marina (Proprietária)', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        novo_usuario('Flávia (Professora)', 'professora@escola.com', Usuario.PAPEL_PROFESSORA)
        novo_usuario('Ana Silva', 'ana@aluno.com', Usuario.PAPEL_ALUNO)

        ana = novo_aluno('Ana Silva', 'Violão', 250.0, email='ana@aluno.com')
        bruno = novo_aluno('Bruno Costa', 'Piano', 300.0)
        carla = novo_aluno('Carla Souza', 'Bateria', 280.0)
        diego = novo_aluno('Diego Lima', 'Violino', 320.0, status='inativo')

        for i in range(4, 0, -1):
            nova_mensalidade(ana, i, True)
        nova_mensalidade(ana, 0, False, vencimento=date.today() + timedelta(days=20))  # pendente

        nova_mensalidade(bruno, 3, True)
        nova_mensalidade(bruno, 2, False)
        nova_mensalidade(bruno, 1, False)

        nova_mensalidade(carla, 2, True)
        nova_mensalidade(carla, 1, False)

        nova_mensalidade(diego, 1, True)

        nova_aula(ana, 5)
        nova_aula(bruno, 95)
        db.session.commit()
        ids = {'ana': ana.id, 'bruno': bruno.id, 'carla': carla.id, 'diego': diego.id}
    return ids


@pytest.fixture
def dados(app):
    return popular_dados(app)


# ---------------------------------------------------------------------------
# Helpers de requisição
# ---------------------------------------------------------------------------
def login(client, email, senha=SENHA):
    return client.post('/login', data={'email': email, 'senha': senha}, follow_redirects=False)


def logar_gestora(client):
    return login(client, 'gestora@escola.com')


def logar_professora(client):
    return login(client, 'professora@escola.com')


def logar_aluno(client):
    return login(client, 'ana@aluno.com')


RE_CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


def obter_csrf(client, url):
    """Pega o token CSRF de um formulário renderizado em ``url``."""
    html = client.get(url).get_data(as_text=True)
    m = RE_CSRF.search(html)
    assert m, f'formulário em {url} não tem csrf_token'
    return m.group(1)


def texto(resposta):
    return resposta.get_data(as_text=True)

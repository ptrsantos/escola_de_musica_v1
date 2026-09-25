"""
Modelo de risco de evasão (app/risco.py): features com corte temporal,
pontuação em Python puro igual ao scikit-learn, motivos, fallback para a regra
e desempenho do lote.

A conftest cria as aplicações com MODELO_EVASAO=None (regra); aqui cada teste
liga o modelo apontando para um JSON escrito em tmp_path.
"""
import json
import math
import time
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app import db
from app.models import Aluno
from app.risco import (FEATURES, caracteristicas, carregar_modelo, motivos, pontuar,
                       probabilidade, regra)

from conftest import criar_app, popular_dados, logar_gestora, texto
from test_desempenho import popular_base_grande, contar_consultas, LIMITE_CONSULTAS, LIMITE_SEGUNDOS

HOJE = date(2026, 9, 17)


def sigmoide(z):
    return 1.0 / (1.0 + math.exp(-z))


def modelo_sintetico(**coefs):
    """Modelo com média 0 e desvio 1 (z = valor bruto), coeficientes só nas
    features indicadas e intercepto -2: P = sigmoide(-2 + soma coef x valor)."""
    return {
        'versao': 1, 'features': list(FEATURES),
        'media': [0.0] * len(FEATURES), 'desvio': [1.0] * len(FEATURES),
        'coef': [coefs.get(f, 0.0) for f in FEATURES], 'intercepto': -2.0,
    }


def app_com_modelo(tmp_path, modelo, nome='modelo.json'):
    caminho = tmp_path / nome
    caminho.write_text(json.dumps(modelo), encoding='utf-8')
    return criar_app(MODELO_EVASAO=str(caminho))


# ---------------------------------------------------------------------------
# caracteristicas(): função pura, com corte temporal
# ---------------------------------------------------------------------------
ALUNO = SimpleNamespace(data_cadastro=datetime(2025, 1, 1), data_nascimento=date(2010, 10, 1))
MENSALIDADES = [
    # (vencimento, valor, status, [(data_pagamento, valor)])
    (date(2025, 1, 10), 200.0, 'pago', [(date(2025, 1, 10), 200.0)]),   # em dia
    (date(2025, 2, 10), 200.0, 'pago', [(date(2025, 3, 2), 200.0)]),    # 20 dias de atraso
    (date(2025, 3, 10), 0.0, 'pago', []),                                # bolsa integral
    (date(2025, 4, 10), 200.0, 'em atraso', []),                         # vencida sem pagamento
    (date(2025, 5, 10), 200.0, 'em atraso', [(date(2025, 5, 20), 100.0)]),  # paga pela metade
    (date(2026, 10, 10), 200.0, 'pendente', []),                         # depois do corte
]
AULAS = [
    (date(2025, 2, 1), 'Presença: 1P/0A · Violão'),
    (date(2025, 3, 1), 'Presença: 0P/1A · Violão'),
    (date(2025, 4, 1), 'Boa evolução (sem marcação)'),
    (date(2026, 9, 30), 'Presença: 1P/0A'),                              # depois do corte
]


def test_caracteristicas_no_corte():
    f = caracteristicas(ALUNO, MENSALIDADES, AULAS, ate=HOJE)
    assert f['n_mens'] == 5                      # a de out/2026 fica fora
    assert f['prop_pagas'] == pytest.approx(3 / 5)   # 2 pagas + bolsa
    assert f['n_atraso'] == 2 and f['prop_atraso'] == pytest.approx(2 / 5)
    assert f['atraso_medio_dias'] == pytest.approx(10.0)   # (0 + 20) / 2; bolsa não entra
    assert f['meses_sem_pagar'] == pytest.approx((HOJE - date(2025, 5, 20)).days / 30.44)
    assert f['valor_medio'] == pytest.approx(800.0 / 5)
    assert f['n_aulas'] == 3 and f['sem_aulas'] == 0
    assert f['taxa_presenca'] == pytest.approx(0.5)        # 1P / (1P + 1A)
    assert f['dias_sem_aula'] == (HOJE - date(2025, 4, 1)).days
    assert f['tempo_casa_meses'] == pytest.approx((HOJE - date(2025, 1, 1)).days / 30.44)
    assert f['idade'] == 15 and f['sem_idade'] == 0
    assert list(f) == FEATURES


def test_caracteristicas_corte_anterior_nao_ve_o_futuro():
    f = caracteristicas(ALUNO, MENSALIDADES, AULAS, ate=date(2025, 2, 15))
    assert f['n_mens'] == 2                      # jan e fev
    assert f['n_atraso'] == 1                    # fev ainda não paga em 15/02 (paga só em 02/03)
    assert f['prop_pagas'] == pytest.approx(0.5)
    assert f['n_aulas'] == 1 and f['taxa_presenca'] == 1.0
    assert f['idade'] == 14


def test_caracteristicas_aluno_sem_nada():
    novo = SimpleNamespace(data_cadastro=None, data_nascimento=None)
    f = caracteristicas(novo, [], [], ate=HOJE)
    assert f['n_mens'] == 0 and f['prop_pagas'] == 0 and f['n_atraso'] == 0
    assert f['sem_aulas'] == 1 and f['dias_sem_aula'] == 365
    assert f['tempo_casa_meses'] == 0 and f['meses_sem_pagar'] == 0
    assert f['idade'] == 0 and f['sem_idade'] == 1
    assert all(isinstance(v, (int, float)) for v in f.values())


def test_regra_calculada_das_features():
    cfg = {'peso_por_atraso': 25, 'peso_sem_aula_recente': 15, 'dias_aula_recente': 60}
    f = caracteristicas(ALUNO, MENSALIDADES, AULAS, ate=HOJE)
    assert regra(f, cfg) == 2 * 25 + 15          # última aula há > 60 dias
    f = caracteristicas(ALUNO, MENSALIDADES, AULAS, ate=date(2025, 4, 15))
    assert regra(f, cfg) == 1 * 25               # abril vencida; aula há 14 dias
    assert regra({'n_atraso': 9, 'sem_aulas': 1, 'dias_sem_aula': 365}, cfg) == 100


# ---------------------------------------------------------------------------
# JSON pontua igual ao scikit-learn
# ---------------------------------------------------------------------------
def test_probabilidade_igual_ao_predict_proba_do_sklearn():
    np = pytest.importorskip('numpy')
    pytest.importorskip('sklearn')
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(7)
    X = rng.normal(size=(200, len(FEATURES))) * rng.uniform(0.1, 50, len(FEATURES))
    X[:, FEATURES.index('sem_idade')] = 1.0          # desvio zero: sklearn usa escala 1
    y = (X[:, 0] + X[:, 3] > np.median(X[:, 0] + X[:, 3])).astype(int)
    pipe = make_pipeline(StandardScaler(), LogisticRegression(class_weight='balanced')).fit(X, y)

    modelo = {
        'features': list(FEATURES),
        'media': pipe[0].mean_.tolist(), 'desvio': pipe[0].scale_.tolist(),
        'coef': pipe[1].coef_[0].tolist(), 'intercepto': float(pipe[1].intercept_[0]),
    }
    esperado = pipe.predict_proba(X[:20])[:, 1]
    for linha, p in zip(X[:20], esperado):
        feats = dict(zip(FEATURES, linha.tolist()))
        assert probabilidade(feats, modelo) == pytest.approx(p, abs=1e-9)


def test_modelo_exportado_no_repositorio_e_valido():
    """O app/modelo_evasao.json versionado tem de bater com FEATURES."""
    app = criar_app(MODELO_EVASAO='modelo_evasao.json')
    with app.app_context():
        modelo = carregar_modelo('modelo_evasao.json')
    assert modelo is not None and modelo['features'] == FEATURES
    assert len(modelo['coef']) == len(modelo['media']) == len(modelo['desvio']) == len(FEATURES)
    assert 0 < probabilidade(dict(zip(FEATURES, modelo['media'])), modelo) < 1


# ---------------------------------------------------------------------------
# Motivos
# ---------------------------------------------------------------------------
def test_motivos_descrevem_o_que_empurrou_o_score():
    modelo = modelo_sintetico(n_atraso=1.0, sem_aulas=1.0, taxa_presenca=-1.0, idade=0.5)
    f = caracteristicas(ALUNO, MENSALIDADES, AULAS, ate=HOJE)
    # n_atraso (2 x 1.0) e idade (15 x 0.5 = 7.5) sobem; taxa 0,5 x -1 desce; sem_aulas = 0 não entra
    assert motivos(f, modelo) == ['15 anos', '2 mensalidade(s) em atraso']

    sem_aula = caracteristicas(ALUNO, MENSALIDADES, [], ate=HOJE)
    # presença 0 % não é motivo de quem não tem aula; 'nenhuma aula registrada' entra pela flag
    assert 'presença de 0% nas aulas' not in motivos(sem_aula, modelo)
    assert 'nenhuma aula registrada' in motivos(sem_aula, modelo)

    regular = caracteristicas(SimpleNamespace(data_cadastro=None, data_nascimento=None), [], [], ate=HOJE)
    assert motivos(regular, modelo_sintetico(n_atraso=1.0)) == ['situação regular']


# ---------------------------------------------------------------------------
# Integração com o app: modelo ligado, fallback, ficha individual, lote
# ---------------------------------------------------------------------------
def test_app_usa_o_modelo_quando_ha_json(tmp_path):
    app = app_com_modelo(tmp_path, modelo_sintetico(n_atraso=1.0))
    ids = popular_dados(app)
    with app.app_context():
        bruno = db.session.get(Aluno, ids['bruno'])   # 2 em atraso: sigmoide(0) = 0,50
        carla = db.session.get(Aluno, ids['carla'])   # 1 em atraso: sigmoide(-1) = 0,27
        ana = db.session.get(Aluno, ids['ana'])       # 0: sigmoide(-2) = 0,12
        assert bruno.risco_score == 50 and bruno.risco == 'alto'
        assert bruno.risco_motivos == ['2 mensalidade(s) em atraso']
        assert carla.risco_score == 27 and carla.risco == 'baixo'   # limiar médio = 35
        assert ana.risco_score == 12 and ana.risco == 'baixo'
        assert ana.risco_motivos == ['situação regular']

    client = app.test_client()
    logar_gestora(client)
    assert client.get('/api/dashboard-data').get_json()['risco'] == {'baixo': 2, 'médio': 0, 'alto': 1}
    html = texto(client.get(f'/aluno/{ids["bruno"]}'))
    assert 'Alto (50)' in html and '2 mensalidade(s) em atraso' in html
    assert client.get('/dashboard').status_code == 200
    assert client.get('/alunos').status_code == 200
    assert client.get('/relatorios').status_code == 200


def test_sem_json_vale_a_regra(tmp_path):
    app = criar_app(MODELO_EVASAO=str(tmp_path / 'nao_existe.json'))
    ids = popular_dados(app)
    with app.app_context():
        bruno = db.session.get(Aluno, ids['bruno'])
        assert bruno.risco_score == 65            # 2 x 25 + 15, a regra de sempre
        assert bruno.risco_motivos == ['2 mensalidade(s) em atraso', 'sem acompanhamento há mais de 60 dias']


def test_json_com_outras_features_e_ignorado(tmp_path):
    modelo = modelo_sintetico(n_atraso=1.0)
    modelo['features'] = ['outra_coisa']
    app = app_com_modelo(tmp_path, modelo)
    ids = popular_dados(app)
    with app.app_context():
        assert db.session.get(Aluno, ids['bruno']).risco_score == 65


def test_pontuar_em_lote_devolve_features_e_probabilidade(tmp_path):
    app = app_com_modelo(tmp_path, modelo_sintetico(n_atraso=1.0))
    ids = popular_dados(app)
    with app.app_context():
        alunos = Aluno.query.all()
        resultado = pontuar(alunos)
        assert set(resultado) == set(ids.values())
        assert resultado[ids['bruno']]['probabilidade'] == pytest.approx(0.5)
        assert resultado[ids['bruno']]['features']['n_atraso'] == 2
        assert resultado[ids['diego']]['score'] == 12


@pytest.mark.slow
@pytest.mark.parametrize('rota', ['/dashboard', '/alunos', '/relatorios', '/api/dashboard-data'])
def test_paginas_com_modelo_continuam_rapidas(tmp_path, rota):
    """Com o modelo ligado, o histórico vem em 3 consultas agrupadas — nunca
    uma por aluno (300 alunos, ~9.900 mensalidades)."""
    app = app_com_modelo(tmp_path, modelo_sintetico(n_atraso=0.1, dias_sem_aula=0.01))
    popular_base_grande(app)
    client = app.test_client()
    logar_gestora(client)
    client.get(rota)
    contador = contar_consultas(app)
    contador['n'] = 0
    inicio = time.perf_counter()
    r = client.get(rota)
    duracao = time.perf_counter() - inicio
    assert r.status_code == 200
    assert duracao < LIMITE_SEGUNDOS, f'{rota}: {duracao:.2f} s ({contador["n"]} consultas)'
    assert contador['n'] < LIMITE_CONSULTAS, f'{rota}: {contador["n"]} consultas SQL'

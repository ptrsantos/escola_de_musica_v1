"""
Indicadores pedagógicos (app/indicadores.py): marcador de presença na
observação, resumo por aluno, alunos mais faltosos, presença por instrumento,
aulas por dia da semana — e as telas que os mostram.
"""
from datetime import date, timedelta
from types import SimpleNamespace

from app import db
from app.models import Aula
from app.indicadores import (contar_presenca, indicadores_pedagogicos, marcar_presenca,
                             resumo_presenca)

from conftest import novo_aluno, logar_gestora, logar_professora, texto


def test_contar_e_marcar_presenca():
    assert contar_presenca('Presença: 1P/0A · Português · diário 77') == (1, 0)
    assert contar_presenca('Presença: 0P/1A') == (0, 1)
    assert contar_presenca('Boa evolução no dedilhado.') == (0, 0)
    assert contar_presenca(None) == (0, 0)

    assert marcar_presenca('Escalas', 'presente') == 'Presença: 1P/0A · Escalas'
    assert marcar_presenca('', 'falta') == 'Presença: 0P/1A'
    assert marcar_presenca(None, 'falta') == 'Presença: 0P/1A'
    assert marcar_presenca('Escalas', '') == 'Escalas'
    assert marcar_presenca('Escalas', None) == 'Escalas'
    assert marcar_presenca('Escalas', 'outra coisa') == 'Escalas'


def test_resumo_presenca():
    aulas = [SimpleNamespace(observacao=o) for o in
             ['Presença: 1P/0A', 'Presença: 0P/1A', 'sem marcação', 'Presença: 2P/1A']]
    r = resumo_presenca(aulas)
    assert r == {'aulas': 3, 'presencas': 3, 'faltas': 2, 'taxa': 0.6}
    assert resumo_presenca([SimpleNamespace(observacao='livre')])['taxa'] is None
    assert resumo_presenca([])['aulas'] == 0


def aula(aluno, dia, obs):
    db.session.add(Aula(aluno_id=aluno.id, professora='Docente', data=dia, observacao=obs))


def test_indicadores_pedagogicos(app):
    with app.app_context():
        seg, ter, qua = date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)   # seg, ter, qua
        ana = novo_aluno('Ana', 'Violão')
        bia = novo_aluno('Bia', 'Violão')
        caio = novo_aluno('Caio', 'Piano')
        dora = novo_aluno('Dora', 'Piano', status='inativo')
        eva = novo_aluno('Eva', 'Canto')                     # só observação livre
        for d, o in [(seg, 'Presença: 1P/0A'), (ter, 'Presença: 1P/0A'), (qua, 'Presença: 0P/1A')]:
            aula(ana, d, o)                                  # 1 falta em 3
        for d, o in [(seg, 'Presença: 0P/1A'), (ter, 'Presença: 0P/1A'), (qua, 'Presença: 1P/0A')]:
            aula(bia, d, o)                                  # 2 faltas em 3
        aula(caio, seg, 'Presença: 1P/0A')                   # 100 %
        aula(caio, seg, 'Presença: 0P/1A')                   # 50 %
        aula(dora, ter, 'Presença: 0P/1A')                   # inativa: fora dos faltosos, conta no instrumento
        aula(dora, ter, 'Presença: 0P/1A')
        aula(eva, qua, 'Trabalhou afinação.')                # sem marcação
        db.session.commit()

        ind = indicadores_pedagogicos(n_faltosos=2)

        faltosos = ind['faltosos']
        # Dora (2 faltas, inativa) fica fora; Caio e Ana empatam em 1 falta e o
        # desempate é a menor presença (Caio 50 % < Ana 67 %)
        assert [f['nome'] for f in faltosos] == ['Bia', 'Caio']
        assert faltosos[0] == {'id': bia.id, 'nome': 'Bia', 'instrumento': 'Violão',
                               'aulas': 3, 'presencas': 1, 'faltas': 2, 'taxa': 1 / 3}

        por_inst = {r['instrumento']: r for r in ind['por_instrumento']}
        assert set(por_inst) == {'Violão', 'Piano'}                   # Canto sem marcação fica fora
        assert por_inst['Violão'] == {'instrumento': 'Violão', 'alunos': 2, 'aulas': 6,
                                      'presencas': 3, 'faltas': 3, 'taxa': 0.5}
        assert por_inst['Piano']['alunos'] == 2 and por_inst['Piano']['taxa'] == 0.25
        assert [r['instrumento'] for r in ind['por_instrumento']] == ['Violão', 'Piano']  # maior taxa primeiro

        dias = ind['por_dia_semana']
        assert dias['dias'][0] == 'Segunda' and len(dias['aulas']) == 7
        assert dias['aulas'][:3] == [4, 4, 3] and dias['aulas'][3:] == [0, 0, 0, 0]
        assert dias['faltas'][:3] == [2, 3, 1]

        todos = indicadores_pedagogicos(n_faltosos=10, apenas_ativos=False)['faltosos']
        assert [f['nome'] for f in todos] == ['Dora', 'Bia', 'Caio', 'Ana']


def test_formulario_de_aula_grava_o_marcador(app, dados):
    client = app.test_client()
    logar_professora(client)
    # Datas relativas a hoje: as aulas do conjunto de teste ficam sempre no
    # passado (hoje - dias_atras), então filtrar por "depois de hoje" isola as
    # duas aulas criadas aqui, em qualquer dia em que a suíte rodar.
    amanha = date.today() + timedelta(days=1)
    depois = date.today() + timedelta(days=2)
    r = client.post('/acompanhamento', data={'aluno_id': dados['ana'], 'data': amanha.isoformat(),
                                             'presenca': 'falta', 'observacao': 'Faltou sem avisar'})
    assert r.status_code == 302
    r = client.post('/acompanhamento', data={'aluno_id': dados['ana'], 'data': depois.isoformat(),
                                             'presenca': '', 'observacao': 'Só observação'})
    assert r.status_code == 302
    with app.app_context():
        obs = [a.observacao for a in Aula.query.filter_by(aluno_id=dados['ana'])
               .filter(Aula.data > date.today()).order_by(Aula.data).all()]
        assert obs == ['Presença: 0P/1A · Faltou sem avisar', 'Só observação']


def test_dashboard_e_ficha_mostram_presenca(app, dados):
    with app.app_context():
        bruno = SimpleNamespace(id=dados['bruno'])
        aula(bruno, date(2026, 9, 14), 'Presença: 0P/1A')
        aula(bruno, date(2026, 9, 15), 'Presença: 1P/0A')
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    html = texto(client.get('/dashboard'))
    assert 'Alunos com mais faltas' in html and 'Presença por instrumento' in html
    assert 'Bruno Costa' in html and 'Aulas por dia da semana' in html
    ficha = texto(client.get(f'/aluno/{dados["bruno"]}'))
    assert '1 presença(s) e 1 falta(s) em 2 aula(s)' in ficha and '50%' in ficha
    ficha_ana = texto(client.get(f'/aluno/{dados["ana"]}'))   # aula do seed: observação livre
    assert 'nenhuma aula com registro de presença' in ficha_ana


# ---------------------------------------------------------------------------
# Ocupação de horários (Aluno.dia_aula_semana / hora_aula)
# ---------------------------------------------------------------------------
def test_ocupacao_horarios(app):
    from datetime import time
    from app.indicadores import ocupacao_horarios
    with app.app_context():
        for nome, dia, hora, status in [('A', 1, time(14, 0), 'ativo'), ('B', 1, time(14, 0), 'ativo'),
                                        ('C', 4, time(9, 0), 'ativo'), ('D', 1, time(14, 0), 'inativo'),
                                        ('E', None, time(14, 0), 'ativo'), ('F', 2, None, 'ativo')]:
            a = novo_aluno(nome, status=status)
            a.dia_aula_semana, a.hora_aula = dia, hora
        db.session.commit()

        o = ocupacao_horarios()
        assert o['horas'] == ['09:00', '14:00'] and o['dias'][1] == 'Terça'
        assert o['celulas'] == [[0, 0, 0, 0, 1, 0, 0], [0, 2, 0, 0, 0, 0, 0]]   # D inativa não conta
        assert o['maximo'] == 2 and o['vagas'] is None
        assert o['intensidade'][1][1] == 1.0 and o['intensidade'][0][4] == 0.5
        assert o['com_horario'] == 3 and o['sem_horario'] == 2               # E sem dia, F sem hora

        o = ocupacao_horarios(vagas_por_horario=4)
        assert o['vagas'] == 4 and o['intensidade'][1][1] == 0.5

        # sem nenhum aluno com horário: grade vazia, sem erro (5 ativos, todos sem dia)
        from app.models import Aluno
        for a in Aluno.query.all():
            a.dia_aula_semana = None
        db.session.commit()
        o = ocupacao_horarios()
        assert o['horas'] == [] and o['celulas'] == [] and o['maximo'] == 0 and o['sem_horario'] == 5


def test_horario_no_cadastro_e_na_ficha(app, dados):
    from datetime import time
    from app.models import Aluno
    client = app.test_client()
    logar_gestora(client)
    r = client.post('/adicionar_aluno', data={'nome': 'Gabi', 'instrumento_id': 1, 'mensalidade_base': '200',
                                              'dia_aula_semana': '3', 'hora_aula': '15:30'})
    assert r.status_code == 302
    with app.app_context():
        gabi = Aluno.query.filter_by(nome='Gabi').one()
        assert (gabi.dia_aula_semana, gabi.hora_aula) == (3, time(15, 30))
        assert gabi.horario_aula == 'Quinta 15:30'
        gabi_id = gabi.id
    assert 'Quinta 15:30' in texto(client.get(f'/aluno/{gabi_id}'))
    html = texto(client.get('/dashboard'))
    assert 'Ocupação de horários' in html and '15:30' in html

    # editar: limpa o dia, mantém a hora; valor inválido vira vazio
    r = client.post(f'/editar_aluno/{gabi_id}', data={'nome': 'Gabi', 'instrumento_id': 1, 'mensalidade_base': '200',
                                                      'dia_aula_semana': '9', 'hora_aula': '15:30', 'status': 'ativo'})
    assert r.status_code == 302
    with app.app_context():
        gabi = db.session.get(Aluno, gabi_id)
        assert gabi.dia_aula_semana is None and gabi.hora_aula == time(15, 30)
        assert gabi.horario_aula == '15:30'
        bruno = db.session.get(Aluno, dados['bruno'])
        assert bruno.horario_aula is None


def test_garantir_colunas_adiciona_as_que_faltam(app):
    """Base criada antes das colunas de horário: init_db/inicializar_db.py acrescenta."""
    from sqlalchemy import inspect, text
    from app import garantir_colunas
    with app.app_context():
        db.session.execute(text('ALTER TABLE aluno DROP COLUMN hora_aula'))
        db.session.execute(text('ALTER TABLE aluno DROP COLUMN dia_aula_semana'))
        db.session.commit()
        assert 'hora_aula' not in {c['name'] for c in inspect(db.engine).get_columns('aluno')}
        assert garantir_colunas() == ['aluno.dia_aula_semana', 'aluno.hora_aula']
        assert garantir_colunas() == []                                   # idempotente
        colunas = {c['name'] for c in inspect(db.engine).get_columns('aluno')}
        assert {'dia_aula_semana', 'hora_aula'} <= colunas

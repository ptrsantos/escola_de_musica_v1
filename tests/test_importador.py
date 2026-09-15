"""
Importador MySQL → Flask: testa ``transformar()`` com dicionários falsos, sem
MySQL nem banco (a função não toca em banco nenhum).
"""
from datetime import date, datetime

import importar_mysql as imp

HOJE = date(2026, 9, 14)


def aluno(mat, **campos):
    base = {'Mat': mat, 'Nome': f'Aluno {mat}', 'DtNascimento': datetime(2010, 1, 1),
            'Endereco': 'Rua X', 'DataEntrada': datetime(2023, 2, 1), 'DtInclusao': datetime(2023, 2, 1)}
    return {**base, **campos}


def mensalidade(mat, descricao='Mensalidade Março/2026', venc=datetime(2026, 3, 10), valor=300.0,
                pagto=None, juros=0, desconto=0, id_=1):
    return {'ID': id_, 'Mat': mat, 'Descricao': descricao, 'DtVenc': venc, 'Valor': valor,
            'DtPagto': pagto, 'Juros': juros, 'Desconto': desconto}


def turma(mat, ano='2026', situacao='F', curso='11', serie='3'):
    return {'Mat': mat, 'AnoLetivo': ano, 'Situacao': situacao, 'idCursos': curso, 'Serie': serie}


def frequencia(mat, data=datetime(2026, 5, 4), marcas=('P', 'A'), disciplina='Português',
               professora='Docente 7', diario=77):
    linha = {'codigo_frequencia': 1, 'matricula_aluno': mat, 'data': data, 'codigo_diario': diario,
             'disciplina': disciplina, 'professora': professora}
    for i, coluna in enumerate(imp.COLUNAS_AULA):
        linha[coluna] = marcas[i] if i < len(marcas) else None
    return linha


def origem(alunos=(), mensalidades=(), aulas=(), docentes=(), planos=None, turmas=()):
    return {
        'alunos': list(alunos), 'mensalidades': list(mensalidades), 'aulas': list(aulas),
        'docentes': list(docentes), 'plano_por_mat': planos or {},
        'primeira_matricula': {}, 'ultima_turma': {t['Mat']: t for t in turmas},
    }


def transformar(o, ano_min=2022):
    return imp.transformar(o, ano_min, HOJE)


# ---------------------------------------------------------------------------
# Utilitários puros
# ---------------------------------------------------------------------------
def test_competencia_vem_da_descricao_ou_do_vencimento():
    assert imp.competencia_da_descricao('Mensalidade Março/2026', date(2026, 4, 10)) == '2026-03'
    assert imp.competencia_da_descricao('MENSALIDADE  fevereiro / 2025', date(2025, 3, 10)) == '2025-02'
    assert imp.competencia_da_descricao('Matrícula 2026', date(2026, 1, 10)) == '2026-01'
    assert imp.competencia_da_descricao(None, date(2026, 1, 10)) == '2026-01'


def test_data_zerada_do_mysql_vira_none():
    assert imp.como_datetime('0000-00-00 00:00:00') is None
    assert imp.como_datetime(None) is None
    assert imp.como_datetime(date(2026, 1, 2)) == datetime(2026, 1, 2)
    assert imp.como_date(datetime(2026, 1, 2, 15, 0)) == date(2026, 1, 2)


def test_de_para_instrumento():
    assert imp.instrumento_para('11', '3') == 'Piano'
    assert imp.instrumento_para('11', 3) == 'Piano'          # série numérica
    assert imp.instrumento_para('04', '2') == 'Flauta'       # curso sem série específica
    assert imp.instrumento_para('20', '9') == imp.INSTRUMENTO_PADRAO   # série fora do de-para
    assert imp.instrumento_para('99', None) == imp.INSTRUMENTO_PADRAO
    assert imp.instrumento_para(None, None) == imp.INSTRUMENTO_PADRAO


# ---------------------------------------------------------------------------
# transformar()
# ---------------------------------------------------------------------------
def test_mensalidade_paga_entra_pelo_valor_liquido_com_juros_no_pagamento():
    o = origem(alunos=[aluno('1')], turmas=[turma('1')], planos={'1': 300},
               mensalidades=[mensalidade('1', valor=300, desconto=50, juros=2.5,
                                         pagto=datetime(2026, 3, 12))])
    alunos, _, descartes = transformar(o)
    m = alunos[0]['mensalidades'][0]
    assert m['competencia'] == '2026-03'
    assert m['valor'] == 250.0
    assert m['vencimento'] == date(2026, 3, 10)
    assert m['pagamento'] == {'data_pagamento': date(2026, 3, 12), 'valor': 252.5,
                              'observacao': 'Importado AC_ id=1 · desconto R$ 50.00 · juros R$ 2.50'}
    assert not descartes


def test_bolsa_integral_fica_sem_pagamento():
    o = origem(alunos=[aluno('1')], turmas=[turma('1')],
               mensalidades=[mensalidade('1', valor=300, desconto=300, pagto=datetime(2026, 3, 12))])
    alunos, _, descartes = transformar(o)
    m = alunos[0]['mensalidades'][0]
    assert m['valor'] == 0.0 and m['pagamento'] is None
    assert descartes['pagamento: bolsa integral (mensalidade líquida = 0, sem lançamento)'] == 1


def test_data_de_pagamento_zerada_e_sem_pagamento():
    o = origem(alunos=[aluno('1')], turmas=[turma('1')], planos={'1': 300},
               mensalidades=[mensalidade('1', pagto='0000-00-00 00:00:00'),
                             mensalidade('1', pagto=None, id_=2)])
    alunos, _, descartes = transformar(o)
    assert [m['pagamento'] for m in alunos[0]['mensalidades']] == [None, None]
    assert not descartes


def test_pagamento_no_futuro_e_descartado_mas_a_mensalidade_fica():
    o = origem(alunos=[aluno('1')], turmas=[turma('1')],
               mensalidades=[mensalidade('1', pagto=datetime(2027, 1, 1))])
    alunos, _, descartes = transformar(o)
    assert alunos[0]['mensalidades'][0]['pagamento'] is None
    assert descartes['pagamento: data fora da faixa (futuro)'] == 1


def test_filtros_de_qualidade_das_mensalidades():
    o = origem(alunos=[aluno('1')], turmas=[turma('1')], mensalidades=[
        mensalidade('1', venc=datetime(2019, 3, 10), id_=1),          # antes de 2022
        mensalidade('1', venc='0000-00-00 00:00:00', id_=2),          # vencimento zerado
        mensalidade('1', valor=0, id_=3),                             # valor zero
        mensalidade('1', valor=9000, id_=4),                          # acima do teto
        mensalidade('1', valor=100, desconto=150, id_=5),             # desconto maior que o valor
        mensalidade('2', id_=6),                                      # aluno inexistente
        mensalidade('1', venc=datetime(2023, 3, 10), id_=7),          # ok, mas cortada por --ano-min
        mensalidade('1', venc=datetime(2024, 3, 10), id_=8),          # ok
    ])
    alunos, _, descartes = transformar(o, ano_min=2024)
    assert [m['vencimento'] for m in alunos[0]['mensalidades']] == [date(2024, 3, 10)]
    assert descartes['mensalidade: vencimento fora de 2022-2027'] == 2
    assert descartes['mensalidade: valor bruto <= 0 ou > 5000, ou desconto > valor'] == 3
    assert descartes['mensalidade: aluno inexistente'] == 1
    assert descartes['mensalidade: vencimento antes de 2024 (--ano-min)'] == 1


def test_status_do_aluno_pela_ultima_matricula():
    o = origem(
        alunos=[aluno('1'), aluno('2'), aluno('3'), aluno('4'), aluno('5')],
        turmas=[turma('1', ano='2026', situacao='F'),      # frequentando este ano → ativo
                turma('2', ano='2026', situacao='D'),      # desistente → inativo
                turma('3', ano='2025', situacao='F'),      # ano passado → inativo
                turma('4', ano='2026', situacao='C')],     # cancelado → inativo
        planos={'1': 250, '2': 250, '3': 250, '4': 250, '5': 250})
    alunos, _, descartes = transformar(o)
    status = {a['mat']: a['status'] for a in alunos}
    assert status == {'1': 'ativo', '2': 'inativo', '3': 'inativo', '4': 'inativo', '5': 'inativo'}
    # sem turma: inativo + instrumento padrão
    sem_turma = next(a for a in alunos if a['mat'] == '5')
    assert sem_turma['instrumento'] == imp.INSTRUMENTO_PADRAO
    assert descartes['aluno: sem turma (inativo + instrumento padrão)'] == 1


def test_instrumento_marcador_e_mensalidade_base():
    o = origem(alunos=[aluno('10'), aluno('11')],
               turmas=[turma('10', curso='20', serie='2'), turma('11', curso='11', serie='1')],
               mensalidades=[mensalidade('11', valor=100, id_=1), mensalidade('11', valor=300, id_=2),
                             mensalidade('11', valor=200, id_=3)],
               planos={'10': 180.0})
    alunos, _, descartes = transformar(o)
    a10, a11 = alunos
    assert a10['instrumento'] == 'Baixo' and a11['instrumento'] == 'Violão'
    assert a10['email'] == '10@ac.sysviolin.local'
    assert a10['mensalidade_base'] == 180.0                # do plano
    assert a11['mensalidade_base'] == 200.0                # mediana das mensalidades
    assert descartes['aluno: mensalidade_base pela mediana (plano ausente/invalido)'] == 1
    assert a10['data_cadastro'] == datetime(2023, 2, 1)
    assert a10['data_nascimento'] == date(2010, 1, 1)


def test_data_cadastro_invalida_e_recuperada():
    o = origem(alunos=[aluno('1', DataEntrada='0000-00-00 00:00:00', DtInclusao=datetime(2022, 5, 5))],
               turmas=[turma('1')], planos={'1': 100})
    o['primeira_matricula'] = {'1': datetime(2021, 8, 1)}
    alunos, _, descartes = transformar(o)
    assert alunos[0]['data_cadastro'] == datetime(2021, 8, 1)
    assert descartes['aluno: data_cadastro recuperada de Turmas/DtInclusao'] == 1


def test_frequencia_vira_aula_com_presenca_no_texto():
    o = origem(alunos=[aluno('1')], turmas=[turma('1')], planos={'1': 100}, aulas=[
        frequencia('1', marcas=('P', 'A', 'PV', None)),
        frequencia('1', marcas=(None,) * 10),                       # sem marcação
        frequencia('1', data=datetime(2027, 1, 1), marcas=('P',)),  # futuro
        frequencia('1', data='0000-00-00', marcas=('P',)),          # data zerada
        frequencia('9', marcas=('P',)),                             # aluno inexistente
        frequencia('1', marcas=('A',), disciplina=None, professora=None),
    ])
    alunos, _, descartes = transformar(o)
    aulas = alunos[0]['aulas']
    assert len(aulas) == 2
    assert aulas[0] == {'data': date(2026, 5, 4), 'professora': 'Docente 7',
                        'observacao': 'Presença: 2P/1A · Português · diário 77'}
    assert aulas[1]['professora'] == 'Docente'
    assert aulas[1]['observacao'] == 'Presença: 0P/1A · disciplina não informada · diário 77'
    assert descartes['aula: linha sem marcação'] == 1
    assert descartes['aula: data fora da faixa'] == 2
    assert descartes['aula: aluno inexistente'] == 1


def test_docentes_viram_professoras_com_marcador():
    o = origem(docentes=[{'C_REG': '5', 'C_NOME': 'Docente 5'}])
    _, professoras, _ = transformar(o)
    assert professoras == [{'nome': 'Docente 5', 'email': 'docente5@ac.sysviolin.local'}]


def test_resumir():
    o = origem(alunos=[aluno('1'), aluno('2')], turmas=[turma('1'), turma('2', situacao='D')],
               planos={'1': 100, '2': 100},
               mensalidades=[mensalidade('1', pagto=datetime(2026, 3, 12), id_=1), mensalidade('2', id_=2)],
               aulas=[frequencia('1')], docentes=[{'C_REG': '5', 'C_NOME': 'Docente 5'}])
    alunos, professoras, _ = transformar(o)
    contagens, por_instrumento = imp.resumir(alunos, professoras)
    assert dict(contagens) == {'alunos': 2, 'alunos ativos': 1, 'mensalidades': 2, 'pagamentos': 1,
                               'aulas': 1, 'professoras (usuarios)': 1}
    assert por_instrumento == {'Piano': 2}

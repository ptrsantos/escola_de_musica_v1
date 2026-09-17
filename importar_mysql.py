"""
Importa a base anonimizada do ERP escolar (MySQL, schema AC_...) para o banco
do SisViolin, seguindo o MAPEAMENTO_AC_FLASK.md.

Uso:
    python importar_mysql.py                # carga completa
    python importar_mysql.py --dry-run      # só lê, transforma e imprime contagens
    python importar_mysql.py --limpar       # remove só o que foi importado antes
    python importar_mysql.py --ano-min 2024 # ignora mensalidades vencidas antes de 2024
    python importar_mysql.py --sem-aulas --sem-professoras
    python importar_mysql.py --so-horarios  # só preenche dia/hora da aula nos alunos já importados

Origem: variável AC_DATABASE_URL (padrão mysql+pymysql://root:root@127.0.0.1:3306/AC_00000000000000).
Destino: o banco que a aplicação usa (DATABASE_URL ou o SQLite de instance/).

A carga é aditiva e re-executável: os registros importados levam um marcador
(Aluno.email = '<Mat>@ac.sysviolin.local', Usuario.email = 'docente<C_REG>@ac...')
e são apagados e recriados a cada execução. Os dados de exemplo do
popular_escola.py não têm esse marcador e ficam intactos. Nada é escrito no MySQL.
"""
import argparse
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time
from urllib.parse import urlsplit

import pymysql
import pymysql.cursors
from sqlalchemy import delete, select

from app import create_app, db
from app.models import Aluno, Aula, Instrumento, Mensalidade, Pagamento, Usuario

# ---------------------------------------------------------------------------
# Parâmetros do mapeamento
# ---------------------------------------------------------------------------
AC_URL_PADRAO = 'mysql+pymysql://root:root@127.0.0.1:3306/AC_00000000000000'
DOMINIO_MARCADOR = '@ac.sysviolin.local'
SENHA_INICIAL_PROFESSORAS = '123456'

VENC_MIN = date(2022, 1, 1)
VENC_MAX = date(2027, 12, 31)
VALOR_MAX = 5000.0
CADASTRO_ANO_MIN, CADASTRO_ANO_MAX = 2000, 2030

INSTRUMENTO_PADRAO = 'Violão'

# Horário fixo da aula semanal (Aluno.dia_aula_semana / hora_aula). O ERP de
# origem é escola regular: não há "horário do aluno", só o turno da turma e as
# datas da frequência. Regra de mapeamento (ver MAPEAMENTO_AC_FLASK.md, 3.4):
# hora pelo turno da última turma; dia = o dia da semana em que o aluno mais
# teve aula registrada (sem frequência, fica sem dia).
TURNO_HORA = {'M': time(8, 0), 'T': time(14, 0), 'N': time(19, 0)}

# (idCursos, Serie) -> instrumento. Serie None = todas as séries do curso.
# Ver MAPEAMENTO_AC_FLASK.md, seção 3.1.
DE_PARA_INSTRUMENTO = {
    ('04', None): 'Flauta',      # Berçário
    ('08', None): 'Flauta',      # Ensino Infantil
    ('02', None): 'Canto',       # Jardim I
    ('03', None): 'Canto',       # Jardim II
    ('11', '1'): 'Violão',       # Ens. Fundamental
    ('11', '2'): 'Violão',
    ('11', '3'): 'Piano',
    ('11', '4'): 'Piano',
    ('11', '5'): 'Teclado',
    ('11', '6'): 'Violino',
    ('11', '7'): 'Bateria',
    ('11', '8'): 'Bateria',
    ('11', '9'): 'Guitarra',
    ('20', '1'): 'Guitarra',     # Ensino Médio
    ('20', '2'): 'Baixo',
    ('20', '3'): 'Saxofone',
    ('50', None): 'Violão',      # Técnico em Administração
    ('56', None): 'Baixo',       # Técnico de RH
    ('45', None): 'Saxofone',    # Técnico em Enfermagem
    ('55', None): 'Bateria',     # Grupo 1
}

MESES = {
    'janeiro': 1, 'fevereiro': 2, 'marco': 3, 'abril': 4, 'maio': 5, 'junho': 6,
    'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12,
}
RE_MENSALIDADE = re.compile(r'mensalidade\s+([a-z]+)\s*/\s*(\d{4})')

PRESENTE = {'P', 'PV'}
AUSENTE = {'A', 'AV'}
COLUNAS_AULA = [f'aula{i}' for i in range(1, 11)]


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def sem_acento(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD', texto or '')
                   if not unicodedata.combining(c)).lower()


def competencia_da_descricao(descricao, vencimento):
    """'Mensalidade março/2026' -> '2026-03'. Sem match, usa o mês do vencimento."""
    m = RE_MENSALIDADE.search(sem_acento(descricao))
    if m and m.group(1) in MESES:
        return f'{int(m.group(2)):04d}-{MESES[m.group(1)]:02d}'
    return vencimento.strftime('%Y-%m')


def como_datetime(valor):
    """PyMySQL devolve datetime para DATETIME válido; uma data que o MySQL
    aceitou mas o Python não representa ('0000-00-00 00:00:00') chega como
    string e vira None."""
    if valor is None or isinstance(valor, str):
        return None
    if isinstance(valor, datetime):
        return valor
    return datetime(valor.year, valor.month, valor.day)


def como_date(valor):
    dt = como_datetime(valor)
    return dt.date() if dt is not None else None


def instrumento_para(id_cursos, serie):
    if id_cursos is None:
        return INSTRUMENTO_PADRAO
    return (DE_PARA_INSTRUMENTO.get((id_cursos, str(serie)))
            or DE_PARA_INSTRUMENTO.get((id_cursos, None))
            or INSTRUMENTO_PADRAO)


def conectar_origem():
    url = urlsplit(os.getenv('AC_DATABASE_URL', AC_URL_PADRAO).strip())
    return pymysql.connect(
        host=url.hostname or '127.0.0.1', port=url.port or 3306,
        user=url.username or 'root', password=url.password or '',
        database=url.path.lstrip('/'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


def consultar(conexao, sql):
    with conexao.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Leitura da origem (só SELECT)
# ---------------------------------------------------------------------------
SQL_ALUNOS = """
    SELECT Mat, Nome, DtNascimento, Endereco, DataEntrada, DtInclusao
      FROM Alunos ORDER BY Mat
"""
# Última matrícula de cada aluno; com duas turmas no mesmo ano, a de menor idClasse1.
SQL_ULTIMA_TURMA = """
    SELECT t.Mat, t.AnoLetivo, t.Situacao, c1.idCursos, c1.Serie, c1.Turno
      FROM Turmas t
      JOIN (SELECT Mat, MAX(AnoLetivo) ano FROM Turmas GROUP BY Mat) u
        ON u.Mat = t.Mat AND u.ano = t.AnoLetivo
      JOIN Classe1 c1 ON c1.idClasse1 = t.idClasse1 AND c1.AnoLetivo = t.AnoLetivo
     ORDER BY t.Mat, CAST(t.idClasse1 AS UNSIGNED)
"""
SQL_PRIMEIRA_MATRICULA = """
    SELECT Mat, MIN(DataMatricula) dt FROM Turmas GROUP BY Mat
"""
SQL_PLANO_ATUAL = """
    SELECT p.Mat, p.ValorParcela
      FROM Planos p
      JOIN (SELECT Mat, MAX(AnoLetivo) ano FROM Planos GROUP BY Mat) u
        ON u.Mat = p.Mat AND u.ano = p.AnoLetivo
"""
SQL_MENSALIDADES = """
    SELECT ID, Mat, Descricao, DtVenc, Valor, DtPagto, Juros, Desconto
      FROM Pagamentos
     WHERE Tipo = 1 AND Descricao LIKE 'Mensalidade %/%'
     ORDER BY Mat, DtVenc, ID
"""
SQL_FREQUENCIA = """
    SELECT f.codigo_frequencia, f.matricula_aluno, f.data, f.codigo_diario,
           f.aula1, f.aula2, f.aula3, f.aula4, f.aula5,
           f.aula6, f.aula7, f.aula8, f.aula9, f.aula10,
           d1.Nome AS disciplina,
           (SELECT doc.C_NOME
              FROM DiarioDoc dd JOIN Docente doc ON doc.C_REG = dd.Professor
             WHERE dd.Diario = f.codigo_diario AND dd.Letivo = f.ano_letivo
             ORDER BY dd.Professor LIMIT 1) AS professora
      FROM Frequencia f
      LEFT JOIN Diarios d ON d.Codigo = f.codigo_diario
      LEFT JOIN Disciplina1 d1 ON d1.Codigo = d.Disciplina AND d1.AnoLetivo = d.Letivo
     ORDER BY f.matricula_aluno, f.data, f.codigo_frequencia
"""
SQL_DOCENTES = """
    SELECT DISTINCT doc.C_REG, doc.C_NOME
      FROM DiarioDoc dd JOIN Docente doc ON doc.C_REG = dd.Professor
     ORDER BY doc.C_REG
"""


def ler_origem(conexao, com_aulas, com_professoras):
    origem = {
        'alunos': consultar(conexao, SQL_ALUNOS),
        'mensalidades': consultar(conexao, SQL_MENSALIDADES),
        'aulas': consultar(conexao, SQL_FREQUENCIA) if com_aulas else [],
        'docentes': consultar(conexao, SQL_DOCENTES) if com_professoras else [],
        'plano_por_mat': {r['Mat']: r['ValorParcela'] for r in consultar(conexao, SQL_PLANO_ATUAL)},
        'primeira_matricula': {r['Mat']: como_datetime(r['dt'])
                               for r in consultar(conexao, SQL_PRIMEIRA_MATRICULA)},
        'ultima_turma': {},
    }
    for r in consultar(conexao, SQL_ULTIMA_TURMA):
        origem['ultima_turma'].setdefault(r['Mat'], r)   # primeira linha = menor idClasse1
    return origem


# ---------------------------------------------------------------------------
# Transformação (não toca em banco nenhum)
# ---------------------------------------------------------------------------
def transformar(origem, ano_min, hoje):
    """Devolve dicionários prontos para virar objetos do Flask e o log de descartes."""
    descartes = Counter()
    ano_atual = str(hoje.year)

    # --- Mensalidades e pagamentos, agrupados por Mat ------------------------
    mensalidades_por_mat = defaultdict(list)
    mats_validas = {a['Mat'] for a in origem['alunos']}
    for r in origem['mensalidades']:
        if r['Mat'] not in mats_validas:
            descartes['mensalidade: aluno inexistente'] += 1
            continue
        venc = como_date(r['DtVenc'])
        if venc is None or not (VENC_MIN <= venc <= VENC_MAX):
            descartes['mensalidade: vencimento fora de 2022-2027'] += 1
            continue
        if venc.year < ano_min:
            descartes[f'mensalidade: vencimento antes de {ano_min} (--ano-min)'] += 1
            continue
        # A mensalidade entra pelo valor líquido (bruto - desconto): o desconto
        # no ERP é a bolsa do aluno, não condição de pagamento antecipado.
        bruto = float(r['Valor'] or 0)
        desconto = float(r['Desconto'] or 0)
        juros = float(r['Juros'] or 0)
        valor = round(bruto - desconto, 2)
        if not (0 < bruto <= VALOR_MAX) or valor < 0:
            descartes['mensalidade: valor bruto <= 0 ou > 5000, ou desconto > valor'] += 1
            continue

        # PyMySQL devolve None para '0000-00-00': vira "sem pagamento".
        pagamento = None
        dt_pagto = como_date(r['DtPagto'])
        if dt_pagto is not None:
            if not (VENC_MIN <= dt_pagto <= hoje):
                descartes['pagamento: data fora da faixa (futuro)'] += 1
            elif valor + juros <= 0:
                # Bolsa integral: baixada no ERP sem dinheiro. Com valor líquido 0
                # a mensalidade já fica 'pago' pela regra do modelo, sem Pagamento.
                descartes['pagamento: bolsa integral (mensalidade líquida = 0, sem lançamento)'] += 1
            else:
                detalhes = ''.join([
                    f' · desconto R$ {desconto:.2f}' if desconto else '',
                    f' · juros R$ {juros:.2f}' if juros else '',
                ])
                pagamento = {'data_pagamento': dt_pagto, 'valor': round(valor + juros, 2),
                             'observacao': f'Importado AC_ id={r["ID"]}{detalhes}'}

        mensalidades_por_mat[r['Mat']].append({
            'competencia': competencia_da_descricao(r['Descricao'], venc),
            'valor': valor,
            'vencimento': venc,
            'pagamento': pagamento,
        })

    # --- Alunos -----------------------------------------------------------------
    alunos = []
    for r in origem['alunos']:
        mat = r['Mat']
        turma = origem['ultima_turma'].get(mat)
        if turma is None:
            descartes['aluno: sem turma (inativo + instrumento padrão)'] += 1
            instrumento, status = INSTRUMENTO_PADRAO, 'inativo'
        else:
            instrumento = instrumento_para(turma['idCursos'], turma['Serie'])
            status = 'ativo' if (turma['AnoLetivo'] == ano_atual and turma['Situacao'] == 'F') else 'inativo'

        base = origem['plano_por_mat'].get(mat)
        base = float(base) if base is not None else 0.0
        if not (0 < base <= VALOR_MAX):
            valores = [m['valor'] for m in mensalidades_por_mat.get(mat, [])]
            base = round(statistics.median(valores), 2) if valores else 0.0
            descartes['aluno: mensalidade_base pela mediana (plano ausente/invalido)'] += 1

        cadastro = como_datetime(r['DataEntrada'])
        if cadastro is None or not (CADASTRO_ANO_MIN <= cadastro.year <= CADASTRO_ANO_MAX):
            cadastro = origem['primeira_matricula'].get(mat) or como_datetime(r['DtInclusao'])
            descartes['aluno: data_cadastro recuperada de Turmas/DtInclusao'] += 1

        hora_aula = TURNO_HORA.get((turma or {}).get('Turno'))
        if hora_aula is None:
            descartes['aluno: sem hora de aula (sem turma ou turno desconhecido)'] += 1

        alunos.append({
            'mat': mat,
            'nome': r['Nome'],
            'email': f'{mat}{DOMINIO_MARCADOR}',
            'data_nascimento': como_date(r['DtNascimento']),
            'endereco': r['Endereco'],
            'instrumento': instrumento,
            'mensalidade_base': base,
            'status': status,
            'data_cadastro': cadastro,
            'hora_aula': hora_aula,
            'dia_aula_semana': None,          # definido depois, pela frequência
            'mensalidades': mensalidades_por_mat.get(mat, []),
            'aulas': [],
        })
    aluno_por_mat = {a['mat']: a for a in alunos}

    # --- Aulas (frequência) ---------------------------------------------------
    for r in origem['aulas']:
        aluno = aluno_por_mat.get(r['matricula_aluno'])
        if aluno is None:
            descartes['aula: aluno inexistente'] += 1
            continue
        marcas = [r[c] for c in COLUNAS_AULA if r[c]]
        n_p = sum(1 for m in marcas if m in PRESENTE)
        n_a = sum(1 for m in marcas if m in AUSENTE)
        if n_p + n_a == 0:
            descartes['aula: linha sem marcação'] += 1
            continue
        data = como_date(r['data'])
        if data is None or not (VENC_MIN <= data <= hoje):
            descartes['aula: data fora da faixa'] += 1
            continue
        aluno['aulas'].append({
            'data': data,
            'professora': r['professora'] or 'Docente',
            'observacao': f'Presença: {n_p}P/{n_a}A · {r["disciplina"] or "disciplina não informada"}'
                          f' · diário {r["codigo_diario"]}',
        })

    # Dia da aula semanal: o dia em que o aluno mais teve aula (empate -> o
    # primeiro da semana). Sem frequência importada, fica sem dia.
    for a in alunos:
        dias = Counter(au['data'].weekday() for au in a['aulas'])
        if dias:
            a['dia_aula_semana'] = min(dias, key=lambda d: (-dias[d], d))
        else:
            descartes['aluno: sem dia de aula (sem frequência)'] += 1

    professoras = [{'nome': d['C_NOME'], 'email': f'docente{d["C_REG"]}{DOMINIO_MARCADOR}'}
                   for d in origem['docentes']]

    return alunos, professoras, descartes


def resumir(alunos, professoras):
    c = Counter()
    c['alunos'] = len(alunos)
    c['alunos ativos'] = sum(1 for a in alunos if a['status'] == 'ativo')
    c['mensalidades'] = sum(len(a['mensalidades']) for a in alunos)
    c['pagamentos'] = sum(1 for a in alunos for m in a['mensalidades'] if m['pagamento'])
    c['aulas'] = sum(len(a['aulas']) for a in alunos)
    c['alunos com dia e hora de aula'] = sum(1 for a in alunos
                                             if a['dia_aula_semana'] is not None and a['hora_aula'])
    c['professoras (usuarios)'] = len(professoras)
    por_instrumento = Counter(a['instrumento'] for a in alunos)
    return c, por_instrumento


# ---------------------------------------------------------------------------
# Escrita no banco do Flask
# ---------------------------------------------------------------------------
def limpar_importados():
    """Apaga só o que veio de importações anteriores (pelo marcador de e-mail)."""
    alunos_importados = select(Aluno.id).where(Aluno.email.like(f'%{DOMINIO_MARCADOR}'))
    mensalidades_importadas = select(Mensalidade.id).where(Mensalidade.aluno_id.in_(alunos_importados))
    removidos = {}
    removidos['pagamentos'] = db.session.execute(
        delete(Pagamento).where(Pagamento.mensalidade_id.in_(mensalidades_importadas))).rowcount
    removidos['aulas'] = db.session.execute(
        delete(Aula).where(Aula.aluno_id.in_(alunos_importados))).rowcount
    removidos['mensalidades'] = db.session.execute(
        delete(Mensalidade).where(Mensalidade.aluno_id.in_(alunos_importados))).rowcount
    removidos['alunos'] = db.session.execute(
        delete(Aluno).where(Aluno.email.like(f'%{DOMINIO_MARCADOR}'))).rowcount
    removidos['usuarios'] = db.session.execute(
        delete(Usuario).where(Usuario.email.like(f'docente%{DOMINIO_MARCADOR}'))).rowcount
    return removidos


def gravar(alunos, professoras):
    instrumentos = {i.nome: i.id for i in Instrumento.query.all()}
    faltando = sorted({a['instrumento'] for a in alunos} - set(instrumentos))
    if faltando:
        raise RuntimeError(f'Instrumentos ausentes no banco do Flask: {faltando}. '
                           'Rode popular_escola.py ou inicializar_db.py antes.')

    for a in alunos:
        aluno = Aluno(nome=a['nome'], email=a['email'], data_nascimento=a['data_nascimento'],
                      endereco=a['endereco'], instrumento_id=instrumentos[a['instrumento']],
                      mensalidade_base=a['mensalidade_base'], status=a['status'],
                      dia_aula_semana=a['dia_aula_semana'], hora_aula=a['hora_aula'])
        if a['data_cadastro'] is not None:
            aluno.data_cadastro = a['data_cadastro']
        for m in a['mensalidades']:
            mensalidade = Mensalidade(competencia=m['competencia'], valor=m['valor'],
                                      vencimento=m['vencimento'])
            if m['pagamento']:
                mensalidade.pagamentos.append(Pagamento(**m['pagamento']))
            mensalidade.atualizar_status()
            aluno.mensalidades.append(mensalidade)
        for au in a['aulas']:
            aluno.aulas.append(Aula(**au))
        db.session.add(aluno)

    for p in professoras:
        usuario = Usuario(nome=p['nome'], email=p['email'], papel=Usuario.PAPEL_PROFESSORA)
        usuario.set_senha(SENHA_INICIAL_PROFESSORAS)
        db.session.add(usuario)

    db.session.flush()


def atualizar_horarios(alunos):
    """Só grava dia_aula_semana/hora_aula nos alunos já importados (pelo
    e-mail marcador), sem apagar nem recriar nada. Devolve quantos mudaram."""
    existentes = {a.email: a for a in Aluno.query.filter(Aluno.email.like(f'%{DOMINIO_MARCADOR}')).all()}
    atualizados = 0
    for a in alunos:
        aluno = existentes.get(a['email'])
        if aluno is None:
            continue
        if (aluno.dia_aula_semana, aluno.hora_aula) != (a['dia_aula_semana'], a['hora_aula']):
            aluno.dia_aula_semana = a['dia_aula_semana']
            aluno.hora_aula = a['hora_aula']
            atualizados += 1
    return atualizados


# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='só lê e imprime contagens; não grava')
    parser.add_argument('--limpar', action='store_true', help='remove o que foi importado antes e sai')
    parser.add_argument('--ano-min', type=int, default=VENC_MIN.year,
                        help='ignora mensalidades vencidas antes deste ano (padrão: %(default)s)')
    parser.add_argument('--sem-aulas', action='store_true', help='não importa a frequência como aulas')
    parser.add_argument('--sem-professoras', action='store_true', help='não cria usuários para as docentes')
    parser.add_argument('--so-horarios', action='store_true',
                        help='só preenche dia/hora da aula nos alunos já importados; não apaga nada')
    args = parser.parse_args(argv)

    app = create_app()
    with app.app_context():
        print(f'Destino: {app.config["SQLALCHEMY_DATABASE_URI"]}')

        if args.limpar:
            removidos = limpar_importados()
            db.session.commit()
            print('Removidos:', dict(removidos))
            return 0

        print(f'Origem:  {os.getenv("AC_DATABASE_URL", AC_URL_PADRAO)}')
        conexao = conectar_origem()
        try:
            origem = ler_origem(conexao, com_aulas=not args.sem_aulas,
                                com_professoras=not args.sem_professoras)
        finally:
            conexao.close()
        print(f'Lidos:   {len(origem["alunos"])} alunos, {len(origem["mensalidades"])} mensalidades, '
              f'{len(origem["aulas"])} linhas de frequência, {len(origem["docentes"])} docentes')

        alunos, professoras, descartes = transformar(origem, args.ano_min, date.today())
        contagens, por_instrumento = resumir(alunos, professoras)

        print('\nDescartes / ajustes:')
        for chave, n in sorted(descartes.items()):
            print(f'  {n:6d}  {chave}')
        print('\nAlunos por instrumento:')
        for nome, n in por_instrumento.most_common():
            print(f'  {n:6d}  {nome}')
        print('\nA gravar:')
        for chave, n in contagens.items():
            print(f'  {n:6d}  {chave}')

        if args.dry_run:
            print('\n--dry-run: nada foi gravado.')
            return 0

        if args.so_horarios:
            atualizados = atualizar_horarios(alunos)
            db.session.commit()
            print(f'\nHorários atualizados em {atualizados} aluno(s) já importado(s); nada apagado.')
            return 0

        try:
            removidos = limpar_importados()
            gravar(alunos, professoras)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        print(f'\nRemovidos da importação anterior: {dict(removidos)}')
        print('Importação concluída.')
        if professoras:
            print(f'Professoras criadas com a senha inicial "{SENHA_INICIAL_PROFESSORAS}" '
                  f'(e-mails docente<N>{DOMINIO_MARCADOR}).')
        return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

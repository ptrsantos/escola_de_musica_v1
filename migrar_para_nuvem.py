"""
Envia os dados do SQLite local (instance/escola_musica.db) para o banco da
nuvem (Postgres do Neon) SEM alterar a estrutura de lá: nenhuma tabela, coluna
ou índice é criada — o script lê a estrutura que já existe no destino e só faz
INSERT nela, na ordem das chaves estrangeiras, preservando os ids.

Uso:
    python migrar_para_nuvem.py --dry-run   # compara estrutura e conta linhas; não grava
    python migrar_para_nuvem.py             # carrega tudo (só se as tabelas do destino estiverem vazias)
    python migrar_para_nuvem.py --limpar    # apaga as LINHAS do destino e carrega tudo de novo
    python migrar_para_nuvem.py --so-horarios   # só atualiza dia/hora da aula dos alunos

--so-horarios é a carga mínima, para quando o destino já tem os dados e só falta
o horário fixo da aula (como em 22/09/2026, quando as colunas existiam lá vazias):
um UPDATE por aluno, casando pelo id, apenas em dia_aula_semana e hora_aula. Não
apaga nada, não toca em nenhuma outra tabela e pula (listando) o aluno cujo valor
no destino já esteja preenchido e diferente — assim um horário cadastrado pela
gestora no app publicado não é sobrescrito pelo SQLite local. Aceita --dry-run.

Origem:  sempre o SQLite de instance/, mesmo que DATABASE_URL esteja definida.
Destino: NUVEM_DATABASE_URL ou, na falta, DATABASE_URL — lidas do ambiente, do
         .env (carregado pelo app) ou do arquivo passado em --env (ex.:
         .env.migracao; qualquer .env.* é ignorado pelo git). Para o Neon a URL
         precisa de ?sslmode=require — se faltar, o script acrescenta. A senha
         nunca é impressa.

Depois da carga, as sequências dos ids do Postgres são avançadas para o maior id
gravado (estado, não estrutura), senão o próximo cadastro pelo app colidiria.
"""
import argparse
import os
import sys
from datetime import time

from sqlalchemy import MetaData, bindparam, create_engine, delete, func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InvalidRequestError

from app import create_app, db
from app.models import Instrumento, Usuario, Aluno, Mensalidade, Pagamento, Aula  # noqa: F401  (registra as tabelas)

TABELAS = ['instrumento', 'usuario', 'aluno', 'mensalidade', 'pagamento', 'aula']  # ordem das FKs
COLUNAS_HORARIO = ['dia_aula_semana', 'hora_aula']   # o que --so-horarios atualiza
LOTE = 1000
ORIGEM_URI = 'sqlite:///escola_musica.db'   # relativo a instance/, como no create_app()


# ---------------------------------------------------------------------------
def url_destino(explicita):
    url = explicita or os.getenv('NUVEM_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        sys.exit('Destino não definido: coloque NUVEM_DATABASE_URL (ou DATABASE_URL) no .env '
                 'de sisviolin/, ou passe --destino URL.')
    url = url.strip()
    if url.startswith('postgres://'):                      # prefixo herdado de alguns provedores
        url = 'postgresql://' + url[len('postgres://'):]
    u = make_url(url)
    connect_args = {}
    if u.drivername.startswith('postgresql') and 'sslmode' not in u.query:
        connect_args['sslmode'] = 'require'
    return u, connect_args


def contar(conn, tabela):
    return conn.execute(select(func.count()).select_from(tabela)).scalar_one()


def somar(conn, tabela, coluna):
    return round(conn.execute(select(func.coalesce(func.sum(tabela.c[coluna]), 0))).scalar_one(), 2)


def comparar_estrutura(meta_origem, meta_destino):
    """Devolve (problemas, avisos). Problema = não dá para carregar sem mudar o destino."""
    problemas, avisos = [], []
    for nome in TABELAS:
        if nome not in meta_destino.tables:
            problemas.append(f'{nome}: tabela não existe no destino')
            continue
        origem = meta_origem.tables[nome].columns
        destino = meta_destino.tables[nome].columns
        so_na_origem = [c.name for c in origem if c.name not in destino]
        so_no_destino = [c for c in destino if c.name not in origem]
        if so_na_origem:
            problemas.append(f'{nome}: colunas da origem sem lugar no destino: {so_na_origem}')
        for c in so_no_destino:
            if not c.nullable and c.default is None and c.server_default is None:
                problemas.append(f'{nome}.{c.name}: só existe no destino e é NOT NULL sem default')
            else:
                avisos.append(f'{nome}.{c.name}: só existe no destino (fica NULL/default)')
    return problemas, avisos


def carregar(engine_origem, meta_origem, engine_destino, meta_destino, limpar):
    postgres = engine_destino.dialect.name == 'postgresql'
    resumo = {}
    with engine_destino.begin() as dst, engine_origem.connect() as src:
        if limpar:
            for nome in reversed(TABELAS):
                apagadas = dst.execute(delete(meta_destino.tables[nome])).rowcount
                print(f'  apagadas {apagadas:6d} linhas de {nome}')
        for nome in TABELAS:
            t_origem, t_destino = meta_origem.tables[nome], meta_destino.tables[nome]
            colunas = [c.name for c in t_origem.columns if c.name in t_destino.columns]
            linhas = src.execute(
                select(*[t_origem.c[c] for c in colunas]).order_by(t_origem.c.id)
            ).mappings().all()
            for i in range(0, len(linhas), LOTE):
                dst.execute(insert(t_destino), [dict(r) for r in linhas[i:i + LOTE]])
            if postgres and linhas:
                # avança a sequência do id (SERIAL) para depois do maior id gravado
                dst.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{nome}', 'id'), "
                    f"(SELECT MAX(id) FROM {nome}))"))
            resumo[nome] = len(linhas)
            print(f'  gravadas {len(linhas):6d} linhas em {nome}')
    return resumo


def como_hora(valor):
    """O SQLite guarda TIME como texto; o Postgres quer um datetime.time.
    Quando a leitura vem pelos metadados o SQLAlchemy já converte — isto é só
    uma rede de segurança para o caso de vir string."""
    if valor is None or isinstance(valor, time):
        return valor
    texto = str(valor).strip()
    return time.fromisoformat(texto.split('.')[0]) if texto else None


def atualizar_horarios(engine_origem, meta_origem, engine_destino, meta_destino, aplicar):
    """UPDATE só em aluno.dia_aula_semana / aluno.hora_aula, casando pelo id.

    Não apaga nada e não toca em nenhuma outra coluna. Devolve o resumo; com
    ``aplicar=False`` apenas simula."""
    t_origem, t_destino = meta_origem.tables['aluno'], meta_destino.tables['aluno']
    faltando = [c for c in COLUNAS_HORARIO if c not in t_destino.columns]
    if faltando:
        sys.exit(f'\nO destino não tem {faltando} em aluno — rode inicializar_db.py lá primeiro. '
                 'Nada foi gravado.')

    with engine_origem.connect() as src:
        origem = {
            linha.id: (linha.dia_aula_semana, como_hora(linha.hora_aula))
            for linha in src.execute(
                select(t_origem.c.id, t_origem.c.dia_aula_semana, t_origem.c.hora_aula)
                .where(t_origem.c.dia_aula_semana.isnot(None)
                       | t_origem.c.hora_aula.isnot(None))
                .order_by(t_origem.c.id))
        }
    with engine_destino.connect() as dst:
        destino = {
            linha.id: (linha.dia_aula_semana, como_hora(linha.hora_aula))
            for linha in dst.execute(
                select(t_destino.c.id, t_destino.c.dia_aula_semana, t_destino.c.hora_aula))
        }

    a_gravar, conflitos, ausentes, iguais = [], [], [], 0
    for id_aluno, (dia, hora) in origem.items():
        if id_aluno not in destino:
            ausentes.append(id_aluno)
        elif destino[id_aluno] == (dia, hora):
            iguais += 1
        elif any(v is not None for v in destino[id_aluno]):
            # já preenchido no destino e diferente: pode ter sido cadastrado lá
            conflitos.append((id_aluno, destino[id_aluno], (dia, hora)))
        else:
            a_gravar.append({'b_id': id_aluno, 'dia': dia, 'hora': hora})

    print(f'\nhorários — alunos com dia ou hora na origem: {len(origem)}')
    print(f'  a atualizar no destino : {len(a_gravar)} '
          f'(com dia: {sum(1 for l in a_gravar if l["dia"] is not None)}, '
          f'com hora: {sum(1 for l in a_gravar if l["hora"] is not None)})')
    print(f'  já iguais lá           : {iguais}')
    print(f'  preenchidos e diferentes (não serão tocados): {len(conflitos)}')
    for id_aluno, la, aqui in conflitos[:10]:
        print(f'    aluno {id_aluno}: destino {la} × origem {aqui}')
    if len(conflitos) > 10:
        print(f'    ... e mais {len(conflitos) - 10}')
    if ausentes:
        print(f'  sem aluno correspondente no destino: {len(ausentes)} {ausentes[:10]}')

    if not aplicar or not a_gravar:
        return {'atualizados': 0, 'iguais': iguais, 'conflitos': len(conflitos),
                'ausentes': len(ausentes)}

    comando = (update(t_destino)
               .where(t_destino.c.id == bindparam('b_id'))
               .values(dia_aula_semana=bindparam('dia'), hora_aula=bindparam('hora')))
    with engine_destino.begin() as dst:
        atualizados = dst.execute(comando, a_gravar).rowcount
    print(f'\n  linhas atualizadas: {atualizados}')

    with engine_destino.connect() as dst:
        com_dia, com_hora = dst.execute(select(
            func.count(t_destino.c.dia_aula_semana),
            func.count(t_destino.c.hora_aula))).one()
    print(f'  conferência no destino -> com dia: {com_dia} | com hora: {com_hora}')
    return {'atualizados': atualizados, 'iguais': iguais, 'conflitos': len(conflitos),
            'ausentes': len(ausentes)}


def relatorio(engine_origem, meta_origem, engine_destino, meta_destino):
    print(f'\n{"tabela":12s} {"origem":>8s} {"destino":>8s}')
    with engine_origem.connect() as src, engine_destino.connect() as dst:
        for nome in TABELAS:
            n_src = contar(src, meta_origem.tables[nome])
            n_dst = contar(dst, meta_destino.tables[nome]) if nome in meta_destino.tables else '-'
            print(f'{nome:12s} {n_src:8d} {str(n_dst):>8s}')
        if all(n in meta_destino.tables for n in ('mensalidade', 'pagamento')):
            print(f'{"Σ mensalidade.valor":22s} {somar(src, meta_origem.tables["mensalidade"], "valor"):>14.2f} '
                  f'{somar(dst, meta_destino.tables["mensalidade"], "valor"):>14.2f}')
            print(f'{"Σ pagamento.valor":22s} {somar(src, meta_origem.tables["pagamento"], "valor"):>14.2f} '
                  f'{somar(dst, meta_destino.tables["pagamento"], "valor"):>14.2f}')


# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='só compara estrutura e conta linhas')
    parser.add_argument('--limpar', action='store_true', help='apaga as linhas do destino antes de carregar')
    parser.add_argument('--so-horarios', action='store_true',
                        help='não carrega nada: só atualiza dia/hora da aula dos alunos no destino')
    parser.add_argument('--destino', help='URL do destino (padrão: NUVEM_DATABASE_URL ou DATABASE_URL do .env)')
    parser.add_argument('--env', metavar='ARQUIVO', help='arquivo .env alternativo com a URL do destino (ex.: .env.migracao)')
    args = parser.parse_args(argv)

    if args.so_horarios and args.limpar:
        sys.exit('--so-horarios e --limpar se excluem: um atualiza duas colunas, o outro '
                 'apaga e recarrega tudo.')

    if args.env:
        from dotenv import load_dotenv
        if not load_dotenv(args.env, override=True):
            sys.exit(f'Arquivo {args.env} não encontrado ou vazio.')

    app = create_app({'SQLALCHEMY_DATABASE_URI': ORIGEM_URI})
    u_destino, connect_args = url_destino(args.destino)
    engine_destino = create_engine(u_destino, connect_args=connect_args)

    with app.app_context():
        engine_origem = db.engine
        meta_origem = db.metadata
        print(f'Origem:  {engine_origem.url}')
        print(f'Destino: {u_destino.render_as_string(hide_password=True)}')
        if engine_origem.url == engine_destino.url:
            sys.exit('Origem e destino são o mesmo banco.')

        meta_destino = MetaData()
        try:
            meta_destino.reflect(bind=engine_destino, only=TABELAS)
        except InvalidRequestError as e:          # alguma tabela não existe lá
            meta_destino.reflect(bind=engine_destino)
            print(f'\nAviso ao ler o destino: {e}')
        print(f'Tabelas no destino: {sorted(meta_destino.tables) or "nenhuma"}')

        problemas, avisos = comparar_estrutura(meta_origem, meta_destino)
        for a in avisos:
            print(f'  aviso: {a}')
        for p in problemas:
            print(f'  PROBLEMA: {p}')
        relatorio(engine_origem, meta_origem, engine_destino, meta_destino)

        if problemas:
            sys.exit('\nEstrutura do destino incompatível — nada foi gravado (o script não altera estrutura).')

        if args.so_horarios:
            atualizar_horarios(engine_origem, meta_origem, engine_destino, meta_destino,
                               aplicar=not args.dry_run)
            print('\n--dry-run: nada foi gravado.' if args.dry_run
                  else '\nHorários atualizados. Nenhuma outra coluna ou tabela foi tocada.')
            return 0

        if args.dry_run:
            print('\n--dry-run: nada foi gravado.')
            return 0

        with engine_destino.connect() as dst:
            ocupadas = [n for n in TABELAS if contar(dst, meta_destino.tables[n]) > 0]
        if ocupadas and not args.limpar:
            sys.exit(f'\nO destino já tem linhas em {ocupadas}. Rode com --limpar para substituir '
                     'tudo pelo conteúdo do SQLite (ou esvazie lá antes). Nada foi gravado.')

        print('\nCarregando' + (' (apagando o destino antes)' if args.limpar else '') + '...')
        carregar(engine_origem, meta_origem, engine_destino, meta_destino, args.limpar)
        print('\nConferência depois da carga:')
        relatorio(engine_origem, meta_origem, engine_destino, meta_destino)
        print('\nCarga concluída.')
        return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

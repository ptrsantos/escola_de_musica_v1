"""
Base de demonstração coerente: uma professora por instrumento.

Pedido da direção (28/09 e 26/10/2026): "na parte 'meus alunos', o professor tem
acesso a outros instrumentos, não somente os alunos próprios". O filtro do app
está certo — a professora vê os alunos para quem deu aula (Aula.professora) —,
mas a massa de demonstração veio de um ERP de escola regular, sem instrumento
por docente: a "Docente 3" deu aula a 95 alunos de 9 instrumentos, e a Flávia
do seed a quatro alunos de quatro instrumentos.

O que o script faz (idempotente: rodar de novo não muda nada):
  1. cada instrumento ganha uma conta de professora (PROFESSORAS, abaixo), que
     é renomeada para "Nome (Instrumento)"; os e-mails e as senhas ficam;
  2. as aulas cujo nome de professora é de uma dessas contas ou de uma
     "Docente N" (a importação) passam para a professora do instrumento do
     aluno — inclusive as do seed e as que o app gravou como
     "Flávia (Professora)";
  3. aula com outro nome (ex.: digitado pela gestora para alguém sem conta) e
     aluno de instrumento fora da lista não são tocados — só aparecem no resumo.
Nada é apagado; as contas "Docente N" que sobram ficam sem alunos. O modelo de
evasão não usa o nome da professora: os números de risco não mudam.

Uso:
    python ajustar_demonstracao.py --dry-run           # SQLite local: mostra o que muda
    python ajustar_demonstracao.py                     # SQLite local: aplica
    python ajustar_demonstracao.py --nuvem --dry-run   # Neon (.env.migracao), sessão só leitura
    python ajustar_demonstracao.py --nuvem             # Neon: aplica numa transação

No Neon, só depois de mostrar o --dry-run ao Flávio e ele aprovar. Depois de
reimportar o MySQL (importar_mysql.py), rodar de novo. A URL nunca é impressa.
"""
import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import bindparam, create_engine, text

from app.routes import nome_de_professora

RAIZ = os.path.dirname(os.path.abspath(__file__))
RE_DOCENTE = re.compile(r'^Docente \d+$')

# instrumento -> (e-mail da conta, nome). Cada instrumento fica com a docente
# que mais deu aula dele na importação (a Docente 3 deu 370 das 609 aulas dela
# em Violino); Teclado é da Flávia do seed (decisão do Flávio, 28/09/2026).
# Nomes fictícios.
PROFESSORAS = {
    'Teclado':  ('professora@escola.com',        'Flávia (Teclado)'),
    'Violino':  ('docente3@ac.sysviolin.local',  'Marina (Violino)'),
    'Piano':    ('docente1@ac.sysviolin.local',  'Helena (Piano)'),
    'Flauta':   ('docente2@ac.sysviolin.local',  'Beatriz (Flauta)'),
    'Saxofone': ('docente16@ac.sysviolin.local', 'Rafael (Saxofone)'),
    'Bateria':  ('docente4@ac.sysviolin.local',  'Renata (Bateria)'),
    'Baixo':    ('docente31@ac.sysviolin.local', 'Luísa (Baixo)'),
    'Violão':   ('docente26@ac.sysviolin.local', 'Patrícia (Violão)'),
    'Guitarra': ('docente28@ac.sysviolin.local', 'André (Guitarra)'),
    'Canto':    ('docente5@ac.sysviolin.local',  'Gabriela (Canto)'),
}


@dataclass
class Plano:
    renomear: list = field(default_factory=list)      # (id, nome atual, nome novo, e-mail)
    aulas: list = field(default_factory=list)         # (id, professora atual, nova, instrumento)
    fora: Counter = field(default_factory=Counter)    # (professora, instrumento) -> aulas não tocadas
    sem_professora: Counter = field(default_factory=Counter)   # instrumento fora da lista -> aulas
    depois: dict = field(default_factory=dict)        # professora -> {instrumento: alunos} após o ajuste


def planejar(conn, professoras=PROFESSORAS):
    """O que muda, sem gravar nada."""
    plano = Plano()
    usuarios = conn.execute(text('SELECT id, nome, email, papel FROM usuario')).mappings().all()
    por_email = {u['email'].lower(): u for u in usuarios}
    faltam = [email for email, _ in professoras.values() if email.lower() not in por_email]
    if faltam:
        raise SystemExit(f'Contas não encontradas: {", ".join(faltam)}')

    # Nomes (já normalizados) cujas aulas pertencem à demonstração.
    da_demonstracao = {nome_de_professora(u['nome']) for u in usuarios
                       if u['papel'] == 'professora' and RE_DOCENTE.match(u['nome'])}
    for email, nome in professoras.values():
        conta = por_email[email.lower()]
        da_demonstracao |= {nome_de_professora(conta['nome']), nome_de_professora(nome)}
        if conta['nome'] != nome:
            plano.renomear.append((conta['id'], conta['nome'], nome, conta['email']))

    alvo = {instrumento: nome for instrumento, (_, nome) in professoras.items()}
    alunos_depois = defaultdict(lambda: defaultdict(set))
    for aula in conn.execute(text(
            'SELECT au.id, au.aluno_id, au.professora, i.nome AS instrumento '
            'FROM aula au JOIN aluno a ON a.id = au.aluno_id '
            'JOIN instrumento i ON i.id = a.instrumento_id ORDER BY au.id')).mappings():
        atual = aula['professora'] or ''
        final = atual
        if nome_de_professora(atual) not in da_demonstracao and not RE_DOCENTE.match(atual):
            plano.fora[(atual, aula['instrumento'])] += 1
        elif aula['instrumento'] not in alvo:
            plano.sem_professora[aula['instrumento']] += 1
        else:
            final = alvo[aula['instrumento']]
            if atual != final:
                plano.aulas.append((aula['id'], atual, final, aula['instrumento']))
        alunos_depois[nome_de_professora(final)][aula['instrumento']].add(aula['aluno_id'])

    for _, nome in professoras.values():
        plano.depois[nome] = {i: len(a) for i, a in alunos_depois[nome_de_professora(nome)].items()}
    return plano


def aplicar(conn, plano):
    """Grava o plano: um UPDATE por conta e um por professora de destino."""
    for id_, _, nome, _ in plano.renomear:
        conn.execute(text('UPDATE usuario SET nome = :nome WHERE id = :id'), {'nome': nome, 'id': id_})
    por_destino = defaultdict(list)
    for id_, _, nova, _ in plano.aulas:
        por_destino[nova].append(id_)
    atualizar = text('UPDATE aula SET professora = :nova WHERE id IN :ids').bindparams(
        bindparam('ids', expanding=True))
    for nova, ids in por_destino.items():
        conn.execute(atualizar, {'nova': nova, 'ids': ids})


def resumo(plano):
    linhas = [f'Contas renomeadas: {len(plano.renomear)}']
    linhas += [f'  #{id_} {de!r} -> {para!r}  ({email})' for id_, de, para, email in plano.renomear]

    linhas.append(f'Aulas reatribuídas: {len(plano.aulas)}')
    pares = Counter((de, para) for _, de, para, _ in plano.aulas)
    linhas += [f'  {n:4d}  {de!r} -> {para!r}' for (de, para), n in sorted(pares.items())]
    # Aulas que não vieram da importação nem do seed: gravadas pelo app publicado.
    do_app = [a for a in plano.aulas if not RE_DOCENTE.match(a[1]) and a[1] != 'Flávia']
    if do_app:
        linhas.append('  gravadas pelo app (conferir uma a uma):')
        linhas += [f'    aula #{id_} ({instr}): {de!r} -> {para!r}' for id_, de, para, instr in do_app]

    if plano.fora:
        linhas.append('Aulas com outro nome de professora (não tocadas):')
        linhas += [f'  {n:4d}  {prof!r} ({instr})' for (prof, instr), n in sorted(plano.fora.items())]
    if plano.sem_professora:
        linhas.append('Aulas de instrumento sem professora na lista (não tocadas):')
        linhas += [f'  {n:4d}  {instr}' for instr, n in sorted(plano.sem_professora.items())]

    linhas.append('Depois do ajuste — alunos de cada professora, por instrumento:')
    for nome, por_instr in plano.depois.items():
        detalhe = ', '.join(f'{i} {n}' for i, n in sorted(por_instr.items())) or 'nenhum aluno'
        aviso = '' if len(por_instr) <= 1 else '   <-- MAIS DE UM INSTRUMENTO'
        linhas.append(f'  {nome}: {detalhe}{aviso}')
    return '\n'.join(linhas)


def motor(nuvem, so_leitura):
    if not nuvem:
        return create_engine('sqlite:///' + os.path.join(RAIZ, 'instance', 'escola_musica.db'))
    from dotenv import dotenv_values
    url = (dotenv_values(os.path.join(RAIZ, '.env.migracao')).get('NUVEM_DATABASE_URL') or '').strip()
    if not url:
        sys.exit('NUVEM_DATABASE_URL não encontrada em .env.migracao.')
    url = url.replace('postgres://', 'postgresql://', 1)
    opcoes = {'sslmode': 'require'} if 'sslmode=' not in url else {}
    if so_leitura:
        opcoes['options'] = '-c default_transaction_read_only=on'
    return create_engine(url, connect_args=opcoes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='só mostra o que mudaria')
    parser.add_argument('--nuvem', action='store_true', help='Neon (URL em .env.migracao) em vez do SQLite local')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

    print(f'Banco: {"Neon (nuvem)" if args.nuvem else "SQLite local"}'
          f'{" — simulação, nada é gravado" if args.dry_run else ""}')
    engine = motor(args.nuvem, so_leitura=args.dry_run)
    with engine.begin() as conn:                  # uma transação: tudo ou nada
        plano = planejar(conn)
        print(resumo(plano))
        if args.dry_run:
            return
        if not plano.renomear and not plano.aulas:
            print('Nada a fazer: a base já está ajustada.')
            return
        aplicar(conn, plano)
    print('Gravado.')


if __name__ == '__main__':
    main()

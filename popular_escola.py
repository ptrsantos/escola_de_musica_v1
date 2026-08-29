"""
Popula o banco com dados de exemplo da escola de música.

Uso:  python popular_escola.py
"""
from datetime import date, timedelta

from app import create_app, db, init_db
from app.models import Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula

app = create_app()
init_db(app)  # cria tabelas + instrumentos padrão

with app.app_context():
    # Limpa dados transacionais para o script ser idempotente
    Pagamento.query.delete()
    Mensalidade.query.delete()
    Aula.query.delete()
    Aluno.query.delete()
    Usuario.query.delete()
    db.session.commit()

    # ---------------- Usuários ----------------
    def novo_usuario(nome, email, papel, senha='123456'):
        u = Usuario(nome=nome, email=email, papel=papel)
        u.set_senha(senha)
        db.session.add(u)

    novo_usuario('Marina (Proprietária)', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
    novo_usuario('Flávia (Professora)', 'professora@escola.com', Usuario.PAPEL_PROFESSORA)
    novo_usuario('Ana Silva', 'ana@aluno.com', Usuario.PAPEL_ALUNO)
    db.session.commit()

    instrumentos = {i.nome: i for i in Instrumento.query.all()}

    # ---------------- Alunos ----------------
    def novo_aluno(nome, instrumento, mensalidade, email=None, nasc=None, endereco=None):
        a = Aluno(nome=nome, instrumento_id=instrumentos[instrumento].id,
                  mensalidade_base=mensalidade, email=email, data_nascimento=nasc,
                  endereco=endereco, status='ativo')
        db.session.add(a)
        return a

    ana = novo_aluno('Ana Silva', 'Violão', 250.0, email='ana@aluno.com',
                     nasc=date(2010, 5, 12), endereco='Rua das Flores, 123')
    bruno = novo_aluno('Bruno Costa', 'Piano', 300.0,
                       nasc=date(2012, 8, 3), endereco='Av. Central, 456')
    carla = novo_aluno('Carla Souza', 'Bateria', 280.0,
                       nasc=date(2009, 1, 20), endereco='Rua Nova, 789')
    diego = novo_aluno('Diego Lima', 'Violino', 320.0,
                       nasc=date(2011, 11, 2), endereco='Rua do Sol, 55')
    elisa = novo_aluno('Elisa Rocha', 'Canto', 260.0,
                       nasc=date(2008, 3, 15), endereco='Rua da Serra, 90')
    db.session.commit()

    hoje = date.today().replace(day=1)

    def add_mensalidade(aluno, meses_atras, pago):
        ref = (hoje - timedelta(days=meses_atras * 30)).replace(day=10)
        m = Mensalidade(aluno_id=aluno.id, competencia=ref.strftime('%Y-%m'),
                        valor=aluno.mensalidade_base, vencimento=ref)
        db.session.add(m)
        db.session.flush()
        if pago:
            db.session.add(Pagamento(mensalidade_id=m.id, valor=aluno.mensalidade_base,
                                     data_pagamento=ref))
        m.atualizar_status()

    # Ana: em dia -> adimplente, baixo risco
    for i in range(3, -1, -1):
        add_mensalidade(ana, i, True)

    # Bruno: 2 meses em atraso -> inadimplente, alto risco
    add_mensalidade(bruno, 3, True)
    add_mensalidade(bruno, 2, False)
    add_mensalidade(bruno, 1, False)

    # Carla: 1 mês em atraso -> risco médio
    add_mensalidade(carla, 2, True)
    add_mensalidade(carla, 1, False)

    # Diego e Elisa: em dia
    for i in range(2, -1, -1):
        add_mensalidade(diego, i, True)
        add_mensalidade(elisa, i, True)

    # ---------------- Aulas ----------------
    db.session.add(Aula(aluno_id=ana.id, professora='Flávia', data=date.today() - timedelta(days=5),
                        observacao='Boa evolução no dedilhado.',
                        orientacao_estudo='Praticar escala de Dó maior 15 min por dia.'))
    db.session.add(Aula(aluno_id=bruno.id, professora='Flávia', data=date.today() - timedelta(days=95),
                        observacao='Faltou às últimas aulas.',
                        orientacao_estudo='Retomar exercícios de leitura rítmica.'))
    db.session.add(Aula(aluno_id=diego.id, professora='Flávia', data=date.today() - timedelta(days=8),
                        observacao='Afinação e postura corretas.',
                        orientacao_estudo='Exercícios de arcada 20 min por dia.'))
    db.session.add(Aula(aluno_id=elisa.id, professora='Flávia', data=date.today() - timedelta(days=12),
                        observacao='Boa projeção vocal.',
                        orientacao_estudo='Aquecimento diário e respiração diafragmática.'))
    db.session.commit()

    print('Banco populado com sucesso!')
    print('\nUsuários de teste (senha 123456):')
    print('  Proprietária -> gestora@escola.com')
    print('  Professora   -> professora@escola.com')
    print('  Aluno        -> ana@aluno.com')

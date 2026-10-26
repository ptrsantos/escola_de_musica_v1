"""
ajustar_demonstracao.py: uma professora por instrumento (pedido da direção,
26/10/2026 — a professora via alunos de outros instrumentos em "Meus alunos").
"""
from datetime import date

from app import db
from app.models import Aluno, Aula, Instrumento, Usuario
from app.routes import ids_alunos_da_professora
from ajustar_demonstracao import aplicar, planejar
from conftest import novo_usuario

PROFESSORAS = {
    'Teclado': ('professora@escola.com', 'Flávia (Teclado)'),
    'Violão':  ('docente3@ac.sysviolin.local', 'Marina (Violão)'),
    'Piano':   ('docente7@ac.sysviolin.local', 'Helena (Piano)'),
}


def montar_base():
    """Docente 3 deu aula a alunos de três instrumentos; a Flávia, de dois (um
    pelo seed, "Flávia", outro pelo app, "Flávia (Professora)"); a gestora
    digitou "Julia" numa aula. A Docente 8 sobra; a Ronaldo não é da demonstração."""
    instrumento = {i.nome: i.id for i in Instrumento.query.all()}
    novo_usuario('Flávia (Professora)', 'professora@escola.com', Usuario.PAPEL_PROFESSORA)
    for n in (3, 7, 8):
        novo_usuario(f'Docente {n}', f'docente{n}@ac.sysviolin.local', Usuario.PAPEL_PROFESSORA)
    novo_usuario('Ronaldo', 'ronaldo@escola.com', Usuario.PAPEL_PROFESSORA)
    alunos = {}
    for nome, instr in [('Ana', 'Violão'), ('Bruno', 'Piano'), ('Tina', 'Teclado'), ('Caio', 'Bateria')]:
        alunos[nome] = Aluno(nome=nome, instrumento_id=instrumento[instr], mensalidade_base=0)
        db.session.add(alunos[nome])
    db.session.flush()
    for professora, aluno in [('Docente 3', 'Ana'), ('Docente 3', 'Bruno'), ('Docente 3', 'Tina'),
                              ('Docente 3', 'Caio'), ('Flávia', 'Ana'),
                              ('Flávia (Professora)', 'Bruno'), ('Julia', 'Ana')]:
        db.session.add(Aula(aluno_id=alunos[aluno].id, professora=professora, data=date(2026, 9, 1),
                            observacao='Presença: 1P/0A'))
    db.session.commit()
    return alunos


def instrumentos_da(email):
    usuario = Usuario.query.filter_by(email=email).one()
    ids = ids_alunos_da_professora(usuario)
    return {a.instrumento.nome for a in Aluno.query.filter(Aluno.id.in_(ids))}


def test_cada_professora_fica_com_um_instrumento(app):
    with app.app_context():
        alunos = montar_base()
        assert instrumentos_da('professora@escola.com') == {'Violão', 'Piano'}   # o defeito

        plano = planejar(db.session.connection(), PROFESSORAS)
        assert [(de, para) for _, de, para, _ in plano.renomear] == [
            ('Flávia (Professora)', 'Flávia (Teclado)'),
            ('Docente 3', 'Marina (Violão)'), ('Docente 7', 'Helena (Piano)')]
        assert plano.depois == {'Flávia (Teclado)': {'Teclado': 1}, 'Marina (Violão)': {'Violão': 1},
                                'Helena (Piano)': {'Piano': 1}}
        aplicar(db.session.connection(), plano)
        db.session.commit()

        assert instrumentos_da('professora@escola.com') == {'Teclado'}
        assert instrumentos_da('docente3@ac.sysviolin.local') == {'Violão'}
        assert instrumentos_da('docente7@ac.sysviolin.local') == {'Piano'}
        assert instrumentos_da('docente8@ac.sysviolin.local') == set()     # sobrou, sem alunos
        # e-mail (o login) não muda; quem não é da demonstração fica como estava
        assert Usuario.query.filter_by(email='professora@escola.com').one().nome == 'Flávia (Teclado)'
        assert Usuario.query.filter_by(email='ronaldo@escola.com').one().nome == 'Ronaldo'
        assert Aula.query.filter_by(professora='Julia').one().aluno_id == alunos['Ana'].id
        # instrumento sem professora na lista: a aula fica com o nome antigo
        assert Aula.query.filter_by(aluno_id=alunos['Caio'].id).one().professora == 'Docente 3'
        assert plano.sem_professora == {'Bateria': 1}
        # o marcador de presença não é tocado
        assert {a.observacao for a in Aula.query.all()} == {'Presença: 1P/0A'}


def test_rodar_de_novo_nao_muda_nada(app):
    with app.app_context():
        montar_base()
        aplicar(db.session.connection(), planejar(db.session.connection(), PROFESSORAS))
        db.session.commit()
        de_novo = planejar(db.session.connection(), PROFESSORAS)
        assert de_novo.renomear == [] and de_novo.aulas == []

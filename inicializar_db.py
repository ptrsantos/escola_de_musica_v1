from app import db, create_app
from app.models import Instrumento


def inicializar_db():
    instrumentos = [
        Instrumento(nome='Violão'),
        Instrumento(nome='Piano'),
        Instrumento(nome='Teclado'),
        Instrumento(nome='Canto'),
        Instrumento(nome='Bateria'),
        Instrumento(nome='Guitarra'),
        Instrumento(nome='Baixo'),
        Instrumento(nome='Saxofone'),
        Instrumento(nome='Violino'),
        Instrumento(nome='Flauta'),
    ]

    for instrumento in instrumentos:
        db.session.add(instrumento)

    db.session.commit()


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
        inicializar_db()
        print('Banco da escola de música inicializado com sucesso.')

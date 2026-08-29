from sqlalchemy import inspect
from app import create_app, db

app = create_app()
print('URI:', app.config['SQLALCHEMY_DATABASE_URI'])

with app.app_context():
    try:
        # Testa a conexao
        conn = db.engine.connect()
        print('Conexao OK')
        conn.close()

        # Lista tabelas existentes ANTES
        insp = inspect(db.engine)
        print('Tabelas antes:', insp.get_table_names())

        # Importa os modelos para registrar no metadata e cria as tabelas
        from app import models  # noqa: F401
        db.create_all()

        insp = inspect(db.engine)
        print('Tabelas depois:', insp.get_table_names())
    except Exception as e:
        print('ERRO:', type(e).__name__, '-', e)

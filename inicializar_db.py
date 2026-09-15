"""
Prepara o banco da aplicação sem apagar nada: cria as tabelas e os índices que
faltam e os instrumentos padrão (só se não houver nenhum).

Uso:  python inicializar_db.py

Serve tanto para uma instalação nova quanto para atualizar uma base já
populada depois de mudanças no modelo (ex.: índices das chaves estrangeiras
adicionados na correção de desempenho). Para recriar os dados de exemplo do
zero, use popular_escola.py (que apaga tudo).
"""
import sys

from app import create_app, db, garantir_indices
from app.models import inicializar_instrumentos

if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    app = create_app()
    print(f'Banco: {app.config["SQLALCHEMY_DATABASE_URI"]}')
    with app.app_context():
        db.create_all()
        criados = garantir_indices()
        inicializar_instrumentos()
    print(f'Índices criados agora: {", ".join(criados) or "nenhum (já existiam)"}')
    print('Banco pronto.')

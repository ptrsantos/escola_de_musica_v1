import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from datetime import datetime

# Carrega variáveis definidas em um arquivo .env (usado no ambiente local).
# Em produção/nuvem as variáveis normalmente já vêm do próprio ambiente,
# e a ausência do arquivo .env é ignorada silenciosamente.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Criar instâncias das extensões sem inicializar
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

# ---------------------------------------------------------------------------
# Parâmetros da regra de ANÁLISE DE RISCO DE EVASÃO.
# A escala não foi definida no levantamento; foi formalizada aqui como regra
# complementar do projeto e pode ser ajustada pelas proprietárias.
# ---------------------------------------------------------------------------
RISK_CONFIG = {
    'peso_por_atraso': 25,        # cada mensalidade vencida em aberto
    'peso_sem_aula_recente': 15,  # sem acompanhamento pedagógico recente
    'dias_aula_recente': 60,      # janela para considerar a aula "recente"
    'limiar_medio': 20,           # score >= => risco médio
    'limiar_alto': 50,            # score >= => risco alto
}


def _resolver_database_uri():
    """Define qual banco a aplicação vai usar.

    - Local (localhost): usa SQLite por padrão quando DATABASE_URL não existe.
    - Nuvem: quando DATABASE_URL está definida (ex.: Postgres do provedor),
      ela é usada. Muitos provedores fornecem a URL com o prefixo
      ``postgres://``, que o SQLAlchemy moderno não reconhece; por isso
      normalizamos para ``postgresql://``.
    """
    database_url = os.getenv('DATABASE_URL')

    # Remove espaços/quebras de linha acidentais (ex.: valor colado com "\n"
    # no final ao configurar a variável de ambiente no provedor de nuvem).
    if database_url:
        database_url = database_url.strip()

    if not database_url:
        # Ambiente local: SQLite dentro da pasta instance/
        return 'sqlite:///escola_musica.db'

    # Ambiente em nuvem: normaliza o prefixo herdado do Heroku/Render/etc.
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

    return database_url


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'chave-secreta-escola-musica')

    database_uri = _resolver_database_uri()
    app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Opções extras apenas para bancos em nuvem (Postgres): pre_ping evita
    # erros por conexões ociosas encerradas pelo provedor.
    if not database_uri.startswith('sqlite'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_pre_ping': True,
            'pool_recycle': 300,
        }

    app.config['RISK_CONFIG'] = RISK_CONFIG

    # Inicializar extensões com a aplicação
    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    login_manager.login_view = 'login'

    # Context processor para disponibilizar a data atual em todos os templates
    @app.context_processor
    def inject_now():
        return {'now': datetime.now}

    # Importar modelos após criar db para evitar importações circulares
    from app.models import Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula

    # Configurar função carregadora do usuário
    @login_manager.user_loader
    def carregar_usuario(id):
        return db.session.get(Usuario, int(id))

    # Registrar rotas
    from app.routes import register_routes
    register_routes(app)

    return app


def init_db(app):
    with app.app_context():
        db.create_all()
        from app.models import inicializar_instrumentos
        inicializar_instrumentos()

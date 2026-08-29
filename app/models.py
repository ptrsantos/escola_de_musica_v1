from datetime import datetime, date
from calendar import monthrange
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from flask_login import UserMixin

from app import db


# ---------------------------------------------------------------------------
# Usuário (ator do sistema). Adaptado do fluxo de caixa com o campo "papel"
# para diferenciar gestora (proprietária), professora e aluno.
# ---------------------------------------------------------------------------
class Usuario(UserMixin, db.Model):
    PAPEL_GESTORA = 'gestora'
    PAPEL_PROFESSORA = 'professora'
    PAPEL_ALUNO = 'aluno'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(200), nullable=False)
    papel = db.Column(db.String(20), nullable=False, default=PAPEL_GESTORA)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def verificar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    @property
    def is_gestora(self):
        return self.papel == self.PAPEL_GESTORA

    @property
    def is_professora(self):
        return self.papel == self.PAPEL_PROFESSORA

    @property
    def is_aluno(self):
        return self.papel == self.PAPEL_ALUNO


# ---------------------------------------------------------------------------
# Instrumento (equivalente à antiga Categoria do fluxo de caixa).
# ---------------------------------------------------------------------------
class Instrumento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), unique=True, nullable=False)
    alunos = db.relationship('Aluno', backref='instrumento', lazy=True)

    def __repr__(self):
        return f"<Instrumento {self.nome}>"


# ---------------------------------------------------------------------------
# Aluno (cadastro acadêmico e financeiro).
# ---------------------------------------------------------------------------
class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=True)
    endereco = db.Column(db.String(240), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    instrumento_id = db.Column(db.Integer, db.ForeignKey('instrumento.id'), nullable=False)
    mensalidade_base = db.Column(db.Float, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default='ativo')
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    mensalidades = db.relationship('Mensalidade', backref='aluno', lazy=True,
                                   cascade='all, delete-orphan')
    aulas = db.relationship('Aula', backref='aluno', lazy=True,
                            cascade='all, delete-orphan')

    # ----------------- utilitários -----------------
    def idade(self):
        if not self.data_nascimento:
            return None
        hoje = date.today()
        return hoje.year - self.data_nascimento.year - (
            (hoje.month, hoje.day) < (self.data_nascimento.month, self.data_nascimento.day)
        )

    # ----------------- Regras financeiras -----------------
    @property
    def mensalidades_em_atraso(self):
        return [m for m in self.mensalidades if m.status == 'em atraso']

    @property
    def inadimplente(self):
        return len(self.mensalidades_em_atraso) > 0

    @property
    def situacao_financeira(self):
        return 'Inadimplente' if self.inadimplente else 'Adimplente'

    @property
    def valor_em_aberto(self):
        return round(sum(m.valor - m.valor_pago for m in self.mensalidades_em_atraso), 2)

    @property
    def ultima_aula(self):
        return max((a.data for a in self.aulas), default=None)

    # ----------------- Análise de risco de evasão -----------------
    @property
    def risco_score(self):
        """Score de 0 a 100 combinando inadimplência (peso maior) e ausência
        de acompanhamento pedagógico recente."""
        cfg = current_app.config['RISK_CONFIG']
        score = len(self.mensalidades_em_atraso) * cfg['peso_por_atraso']
        ultima = self.ultima_aula
        if not ultima or (date.today() - ultima).days > cfg['dias_aula_recente']:
            score += cfg['peso_sem_aula_recente']
        return min(score, 100)

    @property
    def risco(self):
        cfg = current_app.config['RISK_CONFIG']
        score = self.risco_score
        if score >= cfg['limiar_alto']:
            return 'alto'
        if score >= cfg['limiar_medio']:
            return 'médio'
        return 'baixo'

    @property
    def risco_motivos(self):
        cfg = current_app.config['RISK_CONFIG']
        motivos = []
        atrasos = len(self.mensalidades_em_atraso)
        if atrasos:
            motivos.append(f'{atrasos} mensalidade(s) em atraso')
        ultima = self.ultima_aula
        if not ultima:
            motivos.append('nenhuma aula registrada')
        elif (date.today() - ultima).days > cfg['dias_aula_recente']:
            motivos.append(f'sem acompanhamento há mais de {cfg["dias_aula_recente"]} dias')
        if not motivos:
            motivos.append('situação regular')
        return motivos

    def __repr__(self):
        return f"<Aluno {self.nome}>"


# ---------------------------------------------------------------------------
# Mensalidade + Pagamento (equivalentes à antiga Transacao do fluxo de caixa).
# ---------------------------------------------------------------------------
class Mensalidade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    competencia = db.Column(db.String(7), nullable=False)  # 'YYYY-MM'
    valor = db.Column(db.Float, nullable=False)
    vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pendente')  # pago/pendente/em atraso

    pagamentos = db.relationship('Pagamento', backref='mensalidade', lazy=True,
                                 cascade='all, delete-orphan')

    @property
    def valor_pago(self):
        return sum(p.valor for p in self.pagamentos)

    def atualizar_status(self):
        if self.valor_pago >= self.valor:
            self.status = 'pago'
        elif self.vencimento < date.today():
            self.status = 'em atraso'
        else:
            self.status = 'pendente'

    def __repr__(self):
        return f"<Mensalidade {self.competencia} R${self.valor}>"


class Pagamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mensalidade_id = db.Column(db.Integer, db.ForeignKey('mensalidade.id'), nullable=False)
    data_pagamento = db.Column(db.Date, nullable=False, default=date.today)
    valor = db.Column(db.Float, nullable=False)
    observacao = db.Column(db.String(240), nullable=True)


# ---------------------------------------------------------------------------
# Aula (acompanhamento pedagógico: observações e orientações de estudo).
# ---------------------------------------------------------------------------
class Aula(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    professora = db.Column(db.String(120), nullable=False)
    data = db.Column(db.Date, nullable=False, default=date.today)
    observacao = db.Column(db.Text, nullable=True)         # desempenho / conteúdo trabalhado
    orientacao_estudo = db.Column(db.Text, nullable=True)  # o que estudar / praticar

    def __repr__(self):
        return f"<Aula aluno={self.aluno_id} data={self.data}>"


# ---------------------------------------------------------------------------
# Funções utilitárias (adaptadas do fluxo de caixa).
# ---------------------------------------------------------------------------
def format_currency(value):
    return f"R$ {value:.2f}".replace('.', ',')


def get_first_day_of_month():
    today = date.today()
    return date(today.year, today.month, 1)


def get_last_day_of_month():
    today = date.today()
    last_day = monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last_day)


def format_date_for_form(date_obj):
    return date_obj.strftime('%Y-%m-%d') if date_obj else ''


def inicializar_instrumentos():
    """Cria os instrumentos padrão (equivalente ao inicializar_categorias)."""
    if Instrumento.query.first() is None:
        for nome in ['Violão', 'Piano', 'Teclado', 'Canto', 'Bateria',
                     'Guitarra', 'Baixo', 'Saxofone', 'Violino', 'Flauta']:
            db.session.add(Instrumento(nome=nome))
        try:
            db.session.commit()
            print("Instrumentos padrão criados com sucesso!")
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao criar instrumentos padrão: {str(e)}")

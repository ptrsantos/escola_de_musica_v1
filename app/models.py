from datetime import datetime, date
from calendar import monthrange
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from flask_login import UserMixin
from sqlalchemy import func

from app import db


# ---------------------------------------------------------------------------
# Dias da semana (convenção Python weekday(): segunda=0 ... domingo=6).
# Usado no cadastro de horário de aula recorrente e na "agenda do dia".
# ---------------------------------------------------------------------------
DIAS_SEMANA_PT = [
    'Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira',
    'Sexta-feira', 'Sábado', 'Domingo',
]


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

    # Horário de aula recorrente (semanal). Convenção weekday(): segunda=0..domingo=6.
    # NULL = aluno sem horário fixo definido.
    dia_aula_semana = db.Column(db.Integer, nullable=True)
    hora_aula = db.Column(db.Time, nullable=True)

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

    # ----------------- Horário de aula recorrente -----------------
    @property
    def nome_dia_aula(self):
        """Nome do dia da aula por extenso ('Quarta-feira') ou None."""
        if self.dia_aula_semana is None:
            return None
        if 0 <= self.dia_aula_semana <= 6:
            return DIAS_SEMANA_PT[self.dia_aula_semana]
        return None

    @property
    def hora_aula_formatada(self):
        return self.hora_aula.strftime('%H:%M') if self.hora_aula else None

    @property
    def horario_aula_descricao(self):
        """Descrição amigável do horário fixo (ex.: 'Quarta-feira às 15:00')."""
        dia = self.nome_dia_aula
        if not dia:
            return None
        if self.hora_aula:
            return f'{dia} às {self.hora_aula.strftime("%H:%M")}'
        return dia

    @property
    def tem_aula_hoje(self):
        return self.dia_aula_semana is not None and self.dia_aula_semana == date.today().weekday()

    # ----------------- Aniversário -----------------
    @property
    def faz_aniversario_hoje(self):
        if not self.data_nascimento:
            return False
        hoje = date.today()
        return (self.data_nascimento.month, self.data_nascimento.day) == (hoje.month, hoje.day)

    @property
    def faz_aniversario_no_mes(self):
        if not self.data_nascimento:
            return False
        return self.data_nascimento.month == date.today().month

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
    # Padrão brasileiro com separador de milhar (ex.: R$ 1.234,50)
    formatado = f"{value:,.2f}"
    return "R$ " + formatado.replace(',', 'X').replace('.', ',').replace('X', '.')


def get_first_day_of_month():
    today = date.today()
    return date(today.year, today.month, 1)


def get_last_day_of_month():
    today = date.today()
    last_day = monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last_day)


def format_date_for_form(date_obj):
    return date_obj.strftime('%Y-%m-%d') if date_obj else ''


# ---------------------------------------------------------------------------
# Consultas de apoio à página inicial operacional (home).
# ---------------------------------------------------------------------------
def alunos_com_aula_no_dia(dia_semana):
    """Alunos ativos com aula recorrente no dia da semana informado
    (0=segunda..6=domingo), ordenados por horário."""
    return (Aluno.query
            .filter(Aluno.status == 'ativo',
                    Aluno.dia_aula_semana == dia_semana)
            .order_by(Aluno.hora_aula.asc().nullslast(), Aluno.nome)
            .all())


def aniversariantes_do_dia(dia=None):
    """Alunos ativos que fazem aniversário no dia informado (padrão: hoje)."""
    dia = dia or date.today()
    return [a for a in Aluno.query.filter_by(status='ativo').all()
            if a.data_nascimento
            and (a.data_nascimento.month, a.data_nascimento.day) == (dia.month, dia.day)]


def aniversariantes_do_mes(mes=None):
    """Alunos ativos que fazem aniversário no mês informado (padrão: mês atual),
    ordenados por dia."""
    mes = mes or date.today().month
    lista = [a for a in Aluno.query.filter_by(status='ativo').all()
             if a.data_nascimento and a.data_nascimento.month == mes]
    return sorted(lista, key=lambda a: a.data_nascimento.day)


def balanco_entradas(inicio, fim):
    """Soma dos pagamentos (entradas) com data_pagamento no intervalo
    [inicio, fim] inclusive. Retorna float."""
    total = (db.session.query(func.coalesce(func.sum(Pagamento.valor), 0.0))
             .filter(Pagamento.data_pagamento >= inicio,
                     Pagamento.data_pagamento <= fim)
             .scalar())
    return round(float(total or 0.0), 2)


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

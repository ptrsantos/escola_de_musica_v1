from datetime import datetime, date
from calendar import monthrange
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from flask_login import UserMixin
from sqlalchemy import func, select, update, inspect as sa_inspect
from sqlalchemy.orm import column_property

from app import db

DIAS_SEMANA = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']  # date.weekday()


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
    # Horário fixo da aula semanal (colunas criadas no Neon pelo Paulo; no
    # repositório desde 17/09). dia_aula_semana segue date.weekday():
    # 0 = segunda … 6 = domingo. Base do indicador de ocupação de horários.
    dia_aula_semana = db.Column(db.Integer, nullable=True)
    hora_aula = db.Column(db.Time, nullable=True)

    mensalidades = db.relationship('Mensalidade', backref='aluno', lazy=True,
                                   cascade='all, delete-orphan')
    aulas = db.relationship('Aula', backref='aluno', lazy=True,
                            cascade='all, delete-orphan')

    # Colunas calculadas no banco (definidas no fim do arquivo, porque
    # referenciam Mensalidade e Aula): qtd_em_atraso, valor_em_aberto e
    # ultima_aula. Chegam junto com o SELECT do aluno — sem consulta extra.

    # ----------------- utilitários -----------------
    def idade(self):
        if not self.data_nascimento:
            return None
        hoje = date.today()
        return hoje.year - self.data_nascimento.year - (
            (hoje.month, hoje.day) < (self.data_nascimento.month, self.data_nascimento.day)
        )

    @property
    def horario_aula(self):
        """'Terça 14:00', 'Terça', '14:00' ou None — para telas."""
        partes = []
        if self.dia_aula_semana is not None and 0 <= self.dia_aula_semana <= 6:
            partes.append(DIAS_SEMANA[self.dia_aula_semana])
        if self.hora_aula is not None:
            partes.append(self.hora_aula.strftime('%H:%M'))
        return ' '.join(partes) or None

    # ----------------- Regras financeiras -----------------
    @property
    def mensalidades_em_atraso(self):
        return [m for m in self.mensalidades if m.status == 'em atraso']

    @property
    def inadimplente(self):
        return (self.qtd_em_atraso or 0) > 0

    @property
    def situacao_financeira(self):
        return 'Inadimplente' if self.inadimplente else 'Adimplente'

    # ----------------- Análise de risco de evasão -----------------
    # Com o modelo treinado (app/modelo_evasao.json) o score é 100 x P(evadir)
    # da regressão logística — ver app/risco.py. Sem modelo, vale a regra
    # RISK_CONFIG abaixo. As listas (dashboard, relatórios) chamam
    # risco.pontuar(alunos) antes, para o modelo carregar o histórico de todos
    # em três consultas; a ficha individual calcula só para si.
    @property
    def risco_score(self):
        """Score de 0 a 100: modelo de evasão se houver; senão a regra que
        combina inadimplência (peso maior) e ausência de acompanhamento
        pedagógico recente."""
        from app.risco import avaliacao
        modelo = avaliacao(self)
        if modelo is not None:
            return modelo['score']
        cfg = current_app.config['RISK_CONFIG']
        score = (self.qtd_em_atraso or 0) * cfg['peso_por_atraso']
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
        from app.risco import avaliacao
        modelo = avaliacao(self)
        if modelo is not None:
            return modelo['motivos']
        cfg = current_app.config['RISK_CONFIG']
        motivos = []
        atrasos = self.qtd_em_atraso or 0
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
    STATUS = ('pago', 'pendente', 'em atraso')

    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False, index=True)
    competencia = db.Column(db.String(7), nullable=False)  # 'YYYY-MM'
    valor = db.Column(db.Float, nullable=False)
    vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pendente')  # pago/pendente/em atraso

    pagamentos = db.relationship('Pagamento', backref='mensalidade', lazy=True,
                                 cascade='all, delete-orphan')

    # valor_pago: coluna calculada (soma dos pagamentos), definida no fim do arquivo.

    def total_pago(self):
        """Soma dos pagamentos válida em qualquer estado do objeto.

        Usa a coleção ``pagamentos`` quando ela está em memória (objeto ainda
        não gravado, ou pagamento recém-anexado nesta sessão) e a coluna
        calculada ``valor_pago`` quando não está — assim o valor nunca fica
        defasado em relação ao que acabou de ser anexado."""
        estado = sa_inspect(self)
        if estado.transient or estado.pending or 'pagamentos' in estado.dict:
            return sum(p.valor for p in self.pagamentos)
        return self.valor_pago or 0.0

    def atualizar_status(self, hoje=None):
        hoje = hoje or date.today()
        if self.total_pago() >= self.valor:
            self.status = 'pago'
        elif self.vencimento < hoje:
            self.status = 'em atraso'
        else:
            self.status = 'pendente'

    @classmethod
    def atualizar_vencidas(cls, hoje=None):
        """Marca como 'em atraso', em um único UPDATE, toda mensalidade pendente
        cujo vencimento já passou. É a única transição que depende do calendário
        ('pago' só muda ao registrar pagamento), então substitui o antigo laço
        de atualizar_status() sobre a tabela inteira a cada página."""
        hoje = hoje or date.today()
        resultado = db.session.execute(
            update(cls)
            .where(cls.status == 'pendente', cls.vencimento < hoje)
            .values(status='em atraso')
        )
        return resultado.rowcount

    def __repr__(self):
        return f"<Mensalidade {self.competencia} R${self.valor}>"


class Pagamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mensalidade_id = db.Column(db.Integer, db.ForeignKey('mensalidade.id'), nullable=False, index=True)
    data_pagamento = db.Column(db.Date, nullable=False, default=date.today)
    valor = db.Column(db.Float, nullable=False)
    observacao = db.Column(db.String(240), nullable=True)


# ---------------------------------------------------------------------------
# Aula (acompanhamento pedagógico: observações e orientações de estudo).
# ---------------------------------------------------------------------------
class Aula(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False, index=True)
    professora = db.Column(db.String(120), nullable=False)
    data = db.Column(db.Date, nullable=False, default=date.today)
    observacao = db.Column(db.Text, nullable=True)         # desempenho / conteúdo trabalhado
    orientacao_estudo = db.Column(db.Text, nullable=True)  # o que estudar / praticar

    def __repr__(self):
        return f"<Aula aluno={self.aluno_id} data={self.data}>"


# ---------------------------------------------------------------------------
# Colunas calculadas em SQL (column_property).
#
# Cada uma vira uma subconsulta correlacionada dentro do SELECT principal, de
# modo que listar 335 alunos ou 50 mensalidades custa uma consulta, e não uma
# por linha (era o N+1 do defeito nº 3: valor_pago carregava os pagamentos de
# cada uma das ~10 mil mensalidades). Ficam aqui, fora das classes, porque
# referenciam classes declaradas depois da classe dona.
# ---------------------------------------------------------------------------
Mensalidade.valor_pago = column_property(
    select(func.coalesce(func.sum(Pagamento.valor), 0.0))
    .where(Pagamento.mensalidade_id == Mensalidade.id)
    .correlate_except(Pagamento)
    .scalar_subquery()
)

Aluno.qtd_em_atraso = column_property(
    select(func.count(Mensalidade.id))
    .where(Mensalidade.aluno_id == Aluno.id, Mensalidade.status == 'em atraso')
    .correlate_except(Mensalidade)
    .scalar_subquery()
)

Aluno.valor_em_aberto = column_property(
    select(func.coalesce(func.sum(Mensalidade.valor - Mensalidade.valor_pago), 0.0))
    .where(Mensalidade.aluno_id == Aluno.id, Mensalidade.status == 'em atraso')
    .correlate_except(Mensalidade)
    .scalar_subquery()
)

Aluno.ultima_aula = column_property(
    select(func.max(Aula.data))
    .where(Aula.aluno_id == Aluno.id)
    .correlate_except(Aula)
    .scalar_subquery()
)


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


def ultimos_meses(n, hoje=None):
    """['YYYY-MM', ...] dos últimos ``n`` meses, do mais antigo ao atual, contando
    meses de calendário. (Defeito nº 6: a versão anterior subtraía 30 dias por
    mês e, em março/abril, repetia um mês e pulava fevereiro.)"""
    hoje = hoje or date.today()
    meses = []
    for i in range(n - 1, -1, -1):
        ano, mes = divmod(hoje.year * 12 + hoje.month - 1 - i, 12)
        meses.append(f'{ano:04d}-{mes + 1:02d}')
    return meses


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

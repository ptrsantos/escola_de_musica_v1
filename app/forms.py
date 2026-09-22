from flask_wtf import FlaskForm
from wtforms import (StringField, FloatField, DateField, SelectField,
                     EmailField, TextAreaField, SubmitField, TimeField)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Email

from app.models import DIAS_SEMANA_PT


class AlunoForm(FlaskForm):
    nome = StringField('Nome', validators=[
        DataRequired(message='O nome é obrigatório'),
        Length(min=3, max=120, message='O nome deve ter entre 3 e 120 caracteres')
    ])
    instrumento_id = SelectField('Instrumento', coerce=int,
                                 validators=[DataRequired(message='Selecione o instrumento')])
    mensalidade_base = FloatField('Mensalidade (R$)', validators=[
        NumberRange(min=0, message='A mensalidade não pode ser negativa'), Optional()
    ])
    data_nascimento = DateField('Data de nascimento', format='%Y-%m-%d', validators=[Optional()])
    email = EmailField('E-mail', validators=[Optional(), Email(message='E-mail inválido')])
    endereco = StringField('Endereço', validators=[Optional(), Length(max=240)])
    dia_aula_semana = SelectField(
        'Dia da aula', coerce=int, validators=[Optional()],
        choices=[(-1, 'Sem horário fixo')] + list(enumerate(DIAS_SEMANA_PT)),
    )
    hora_aula = TimeField('Horário da aula', format='%H:%M', validators=[Optional()])
    submit = SubmitField('Salvar')


class MensalidadeForm(FlaskForm):
    aluno_id = SelectField('Aluno', coerce=int,
                           validators=[DataRequired(message='Selecione o aluno')])
    competencia = StringField('Competência (AAAA-MM)', validators=[DataRequired()])
    valor = FloatField('Valor (R$)', validators=[
        DataRequired(message='O valor é obrigatório'),
        NumberRange(min=0.01, message='O valor deve ser maior que zero')
    ])
    vencimento = DateField('Vencimento', format='%Y-%m-%d',
                           validators=[DataRequired(message='A data de vencimento é obrigatória')])
    submit = SubmitField('Registrar mensalidade')


class AulaForm(FlaskForm):
    aluno_id = SelectField('Aluno', coerce=int,
                           validators=[DataRequired(message='Selecione o aluno')])
    professora = StringField('Professora', validators=[Optional(), Length(max=120)])
    data = DateField('Data', format='%Y-%m-%d',
                     validators=[DataRequired(message='A data é obrigatória')])
    observacao = TextAreaField('Observações da aula', validators=[Optional()])
    orientacao_estudo = TextAreaField('O que estudar', validators=[Optional()])
    submit = SubmitField('Salvar acompanhamento')

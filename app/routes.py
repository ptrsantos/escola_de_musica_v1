import re
from datetime import datetime, date
from functools import wraps

from flask import render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, contains_eager, selectinload

from app import db
from app.models import (Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula,
                        format_date_for_form, ultimos_meses)
from app.risco import pontuar
from app.indicadores import (contar_presenca, indicadores_pedagogicos, marcar_presenca,
                             ocupacao_horarios, resumo_presenca, sem_marcador)

MENSALIDADES_POR_PAGINA = 50
ATENCAO_POR_PAGINA = 10        # dashboard: "alunos que pedem atenção", 10 por página


def papeis_required(*papeis):
    """Restringe uma rota a determinados papéis (gestora/professora/aluno)."""
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.papel not in papeis:
                flash('Você não possui permissão para acessar esta área.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def parse_date(value):
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


def parse_hora(value):
    return datetime.strptime(value, '%H:%M').time() if value else None


def parse_dia_semana(value):
    """'0'..'6' (segunda..domingo) -> int; vazio ou inválido -> None."""
    try:
        dia = int(value)
    except (TypeError, ValueError):
        return None
    return dia if 0 <= dia <= 6 else None


def atualizar_status_vencidos():
    """Antes de qualquer página financeira: pendente → em atraso, em um UPDATE."""
    Mensalidade.atualizar_vencidas()
    db.session.commit()


def totais_financeiros():
    """(previsto, recebido) somados no banco. Recebido é a soma de todos os
    pagamentos — o mesmo que somar valor_pago de todas as mensalidades."""
    previsto = db.session.scalar(select(func.coalesce(func.sum(Mensalidade.valor), 0.0)))
    recebido = db.session.scalar(select(func.coalesce(func.sum(Pagamento.valor), 0.0)))
    return previsto, recebido


def nome_de_professora(nome):
    """Nome comparável de uma professora: sem maiúsculas, sem espaços nas pontas
    e **sem o rótulo entre parênteses no fim** — 'Flávia (Professora)' no cadastro
    do usuário e 'Flávia' na aula são a mesma pessoa (é assim nos dados de
    demonstração). Nos dados importados os dois lados já são iguais ('Docente 7')."""
    return re.sub(r'\s*\([^)]*\)\s*$', '', (nome or '').strip()).strip().lower()


def ids_alunos_da_professora(usuario=None):
    """Ids dos alunos de uma professora — os alunos para quem ela deu aula.

    O vínculo é o que o modelo tem hoje: ``Aula.aluno_id`` + ``Aula.professora``.
    Como ``Aula.professora`` é texto livre, sem chave estrangeira para ``Usuario``
    (defeito nº 9), o casamento é pelo nome. A comparação é feita em Python sobre
    os nomes distintos de ``aula`` (algumas dezenas) para poder normalizar os dois
    lados; são duas consultas baratas. Para que toda aula nova case exatamente, o
    formulário de acompanhamento grava o nome do usuário logado quando quem
    registra é a professora.
    """
    usuario = usuario or current_user
    alvo = nome_de_professora(usuario.nome)
    if not alvo:
        return []
    nomes = [nome for nome in db.session.scalars(select(Aula.professora).distinct())
             if nome_de_professora(nome) == alvo]
    if not nomes:
        return []
    return list(db.session.scalars(
        select(Aula.aluno_id).where(Aula.professora.in_(nomes)).distinct()))


def escopo_de_alunos():
    """Quais alunos o usuário logado pode ver: ``None`` = a escola inteira
    (gestora); lista de ids = só os alunos dela (professora). Lista vazia é um
    escopo válido e legítimo: professora que ainda não registrou nenhuma aula."""
    return ids_alunos_da_professora() if current_user.is_professora else None


def pode_ver_aluno(aluno_id, escopo=None):
    """A professora só enxerga os alunos dela — inclusive digitando a URL."""
    if not current_user.is_professora:
        return True
    escopo = escopo if escopo is not None else ids_alunos_da_professora()
    return aluno_id in escopo


def register_routes(app):
    # =================================================================
    # Home / autenticação (mesma estrutura do fluxo de caixa)
    # =================================================================
    @app.route('/')
    def home():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return render_template('index.html')

    @app.route('/registrar', methods=['GET', 'POST'])
    def registrar():
        # Cadastro de usuários NÃO é público. Duas situações permitem acesso:
        #   1. Instalação nova, sem nenhum usuário: a primeira conta criada é
        #      obrigatoriamente a gestora (bootstrap).
        #   2. Depois disso, apenas a gestora logada cadastra novos usuários
        #      e escolhe o perfil de cada um.
        # Antes, qualquer visitante criava uma conta e escolhia ser gestora.
        primeiro_acesso = Usuario.query.first() is None
        if not primeiro_acesso:
            if not current_user.is_authenticated:
                flash('Faça login para continuar.', 'warning')
                return redirect(url_for('login', next=request.path))
            if not current_user.is_gestora:
                flash('Apenas a gestora pode cadastrar usuários.', 'danger')
                return redirect(url_for('dashboard'))

        if request.method == 'POST':
            nome = (request.form.get('nome') or '').strip()
            email = (request.form.get('email') or '').strip().lower()
            senha = request.form.get('senha') or ''

            if primeiro_acesso:
                papel = Usuario.PAPEL_GESTORA
            else:
                papel = request.form.get('papel', Usuario.PAPEL_ALUNO)
                if papel not in (Usuario.PAPEL_GESTORA, Usuario.PAPEL_PROFESSORA,
                                 Usuario.PAPEL_ALUNO):
                    papel = Usuario.PAPEL_ALUNO

            if not nome or not email or len(senha) < 6:
                flash('Preencha nome, e-mail e uma senha com pelo menos 6 caracteres.', 'danger')
                return render_template('registrar.html', primeiro_acesso=primeiro_acesso)

            if Usuario.query.filter_by(email=email).first():
                flash('Já existe um usuário com este e-mail.', 'warning')
                return render_template('registrar.html', primeiro_acesso=primeiro_acesso)

            novo_usuario = Usuario(nome=nome, email=email, papel=papel)
            novo_usuario.set_senha(senha)
            try:
                db.session.add(novo_usuario)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao criar conta: {str(e)}', 'danger')
                return render_template('registrar.html', primeiro_acesso=primeiro_acesso)

            if primeiro_acesso:
                flash('Conta da gestora criada. Faça login para continuar.', 'success')
                return redirect(url_for('login'))
            flash(f'Usuário "{nome}" cadastrado como {papel}.', 'success')
            return redirect(url_for('registrar'))

        return render_template('registrar.html', primeiro_acesso=primeiro_acesso)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            email = (request.form.get('email') or '').strip().lower()
            senha = request.form.get('senha')
            usuario = Usuario.query.filter_by(email=email).first()
            if usuario and usuario.verificar_senha(senha):
                login_user(usuario)
                next_page = request.args.get('next')
                return redirect(next_page or url_for('dashboard'))
            flash('Email ou senha inválidos.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Você saiu do sistema.', 'info')
        return redirect(url_for('home'))

    # =================================================================
    # Dashboard / painel gerencial
    # =================================================================
    @app.route('/dashboard')
    @login_required
    def dashboard():
        if current_user.is_aluno:
            return redirect(url_for('minha_area'))

        # A professora não vê o financeiro da escola (decisão da direção,
        # 22/09/2026): o painel dela traz os mesmos indicadores pedagógicos e de
        # risco, porém restritos aos alunos dela. Sem financeiro, também não há
        # motivo para disparar o UPDATE de mensalidades vencidas.
        gestora = current_user.is_gestora
        escopo = escopo_de_alunos()
        if gestora:
            atualizar_status_vencidos()

        # Os alunos vêm com qtd_em_atraso / valor_em_aberto / ultima_aula já
        # calculados no SELECT (column_property) — risco e inadimplência são
        # aritmética em memória, sem tocar no banco de novo.
        consulta = Aluno.query.filter_by(status='ativo').options(joinedload(Aluno.instrumento))
        if escopo is not None:
            consulta = consulta.filter(Aluno.id.in_(escopo))
        alunos = consulta.all()
        pontuar(alunos)   # modelo de evasão em lote (3 consultas para todos)

        # Distribuição de alunos por instrumento (para o gráfico de pizza)
        q_instrumentos = db.session.query(Instrumento.nome, func.count(Aluno.id)).join(Aluno)
        if escopo is not None:
            q_instrumentos = q_instrumentos.filter(Aluno.id.in_(escopo))
        por_instrumento = q_instrumentos.group_by(Instrumento.nome).all()
        dados_instrumentos = {
            'nomes': [nome for nome, _ in por_instrumento],
            'valores': [qtd for _, qtd in por_instrumento],
        }

        # Contagem por nível de risco (para o gráfico de barras)
        riscos = {'baixo': 0, 'médio': 0, 'alto': 0}
        for a in alunos:
            riscos[a.risco] += 1

        # Presença: faltosos, por instrumento e por dia da semana (1 consulta)
        pedagogico = indicadores_pedagogicos(n_faltosos=10, alunos_ids=escopo)
        # Ocupação: alunos ativos por dia x hora da aula (1 consulta)
        ocupacao = ocupacao_horarios(app.config['OCUPACAO_CONFIG']['vagas_por_horario'],
                                     alunos_ids=escopo)

        contexto = dict(
            total_alunos=len(alunos),
            dados_instrumentos=dados_instrumentos,
            riscos=riscos,
            faltosos=pedagogico['faltosos'],
            presenca_instrumento=pedagogico['por_instrumento'],
            aulas_dia_semana=pedagogico['por_dia_semana'],
            ocupacao=ocupacao,
        )

        if gestora:
            inadimplentes = sum(1 for a in alunos if a.inadimplente)
            total_atrasadas = db.session.scalar(
                select(func.count()).select_from(Mensalidade)
                .where(Mensalidade.status == 'em atraso'))
            total_previsto, total_recebido = totais_financeiros()

            # Recebido nos últimos 6 meses (para o gráfico de linha) — recebido
            # por mês de referência (competência), agrupado no banco
            seis_meses = ultimos_meses(6)
            recebido_por_mes = dict(db.session.execute(
                select(Mensalidade.competencia, func.sum(Pagamento.valor))
                .join(Pagamento, Pagamento.mensalidade_id == Mensalidade.id)
                .where(Mensalidade.competencia.in_(seis_meses))
                .group_by(Mensalidade.competencia)
            ).all())

            contexto.update(
                adimplentes=len(alunos) - inadimplentes,
                inadimplentes=inadimplentes,
                total_atrasadas=total_atrasadas,
                total_previsto=total_previsto,
                total_recebido=total_recebido,
                dados_recebido={
                    'meses': seis_meses,
                    'valores': [round(recebido_por_mes.get(ref, 0.0), 2) for ref in seis_meses],
                },
                # Alunos que pedem atenção (risco médio ou alto) — traz situação
                # financeira e motivos de pagamento, então é só da gestora.
                alunos_atencao=sorted((a for a in alunos if a.risco != 'baixo'),
                                      key=lambda a: a.risco_score, reverse=True),
                atencao_por_pagina=ATENCAO_POR_PAGINA,
            )

        return render_template('dashboard.html', **contexto)

    @app.route('/api/dashboard-data')
    @papeis_required('gestora', 'professora')
    def dashboard_data():
        gestora = current_user.is_gestora
        escopo = escopo_de_alunos()
        if gestora:
            atualizar_status_vencidos()

        consulta = Aluno.query.filter_by(status='ativo')
        q_instrumentos = db.session.query(Instrumento.nome, func.count(Aluno.id)).join(Aluno)
        if escopo is not None:
            consulta = consulta.filter(Aluno.id.in_(escopo))
            q_instrumentos = q_instrumentos.filter(Aluno.id.in_(escopo))

        alunos = consulta.all()
        pontuar(alunos)
        riscos = {'baixo': 0, 'médio': 0, 'alto': 0}
        for a in alunos:
            riscos[a.risco] += 1

        dados = {
            'risco': riscos,
            'instrumentos': dict(q_instrumentos.group_by(Instrumento.nome).all()),
        }
        if gestora:   # inadimplência é da escola: fora da resposta da professora
            inadimplentes = sum(1 for a in alunos if a.inadimplente)
            dados['financeiro'] = {'Adimplentes': len(alunos) - inadimplentes,
                                   'Inadimplentes': inadimplentes}
        return jsonify(dados)

    # =================================================================
    # Alunos (equivalente às "transações"/"categorias" do original)
    # =================================================================
    @app.route('/alunos')
    @papeis_required('gestora', 'professora')
    def alunos():
        escopo = escopo_de_alunos()
        if escopo is None:
            atualizar_status_vencidos()
        busca = (request.args.get('busca') or '').strip()
        consulta = Aluno.query.options(joinedload(Aluno.instrumento)).order_by(Aluno.nome)
        if escopo is not None:                 # professora: só os alunos dela
            consulta = consulta.filter(Aluno.id.in_(escopo))
        if busca:
            consulta = consulta.filter(Aluno.nome.ilike(f'%{busca}%'))
        alunos = consulta.all()
        pontuar(alunos)
        return render_template(
            'alunos.html',
            alunos=alunos,
            instrumentos=Instrumento.query.order_by(Instrumento.nome).all(),
            busca=busca,
        )

    @app.route('/adicionar_aluno', methods=['POST'])
    @papeis_required('gestora')
    def adicionar_aluno():
        try:
            aluno = Aluno(
                nome=request.form['nome'].strip(),
                instrumento_id=int(request.form['instrumento_id']),
                mensalidade_base=float(request.form.get('mensalidade_base') or 0),
                data_nascimento=parse_date(request.form.get('data_nascimento')),
                email=(request.form.get('email') or '').strip().lower() or None,
                endereco=request.form.get('endereco'),
                status='ativo',
                dia_aula_semana=parse_dia_semana(request.form.get('dia_aula_semana')),
                hora_aula=parse_hora(request.form.get('hora_aula')),
            )
            db.session.add(aluno)
            db.session.commit()
            flash('Aluno cadastrado com sucesso!', 'success')
            return redirect(url_for('aluno_detalhe', id=aluno.id))
        except (KeyError, ValueError):
            flash('Preencha corretamente os campos obrigatórios (nome e instrumento).', 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar aluno: {str(e)}', 'danger')
        return redirect(url_for('alunos'))

    @app.route('/aluno/<int:id>')
    @login_required
    def aluno_detalhe(id):
        if not current_user.is_professora:
            atualizar_status_vencidos()
        aluno = db.get_or_404(Aluno, id)
        # Aluno só acessa a própria ficha
        if current_user.is_aluno:
            vinculo = Aluno.query.filter_by(email=current_user.email).first()
            if not vinculo or vinculo.id != aluno.id:
                abort(403)
        # Professora só acessa a ficha dos alunos dela — inclusive pela URL
        if not pode_ver_aluno(aluno.id):
            abort(403)
        instrumentos = Instrumento.query.order_by(Instrumento.nome).all()
        return render_template('aluno_detalhe.html', aluno=aluno, instrumentos=instrumentos,
                               presenca=resumo_presenca(aluno.aulas))

    @app.route('/editar_aluno/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def editar_aluno(id):
        aluno = db.get_or_404(Aluno, id)
        try:
            aluno.nome = request.form['nome'].strip()
            aluno.instrumento_id = int(request.form['instrumento_id'])
            aluno.mensalidade_base = float(request.form.get('mensalidade_base') or 0)
            aluno.data_nascimento = parse_date(request.form.get('data_nascimento'))
            aluno.email = (request.form.get('email') or '').strip().lower() or None
            aluno.endereco = request.form.get('endereco')
            aluno.status = request.form.get('status', 'ativo')
            aluno.dia_aula_semana = parse_dia_semana(request.form.get('dia_aula_semana'))
            aluno.hora_aula = parse_hora(request.form.get('hora_aula'))
            db.session.commit()
            flash('Cadastro atualizado com sucesso!', 'success')
        except (KeyError, ValueError):
            flash('Dados inválidos.', 'danger')
        return redirect(url_for('aluno_detalhe', id=aluno.id))

    @app.route('/excluir_aluno/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def excluir_aluno(id):
        aluno = db.get_or_404(Aluno, id)
        nome = aluno.nome
        db.session.delete(aluno)
        db.session.commit()
        flash(f'Aluno "{nome}" removido com sucesso!', 'success')
        return redirect(url_for('alunos'))

    # =================================================================
    # Financeiro (mensalidades e pagamentos)
    # =================================================================
    @app.route('/financeiro')
    @papeis_required('gestora')
    def financeiro():
        atualizar_status_vencidos()

        # Filtros (querystring) + paginação: com ~10 mil mensalidades a página
        # inteira tinha 11 MB de HTML.
        status = request.args.get('status') or ''
        if status not in Mensalidade.STATUS:
            status = ''
        busca = (request.args.get('busca') or '').strip()
        pagina = request.args.get('page', 1, type=int)

        consulta = (Mensalidade.query.join(Aluno)
                    .options(contains_eager(Mensalidade.aluno))
                    .order_by(Mensalidade.vencimento.desc(), Mensalidade.id.desc()))
        if status:
            consulta = consulta.filter(Mensalidade.status == status)
        if busca:
            consulta = consulta.filter(Aluno.nome.ilike(f'%{busca}%'))
        paginacao = consulta.paginate(page=pagina, per_page=MENSALIDADES_POR_PAGINA, error_out=False)

        return render_template(
            'financeiro.html',
            mensalidades=paginacao.items,
            paginacao=paginacao,
            status=status,
            busca=busca,
            alunos=(Aluno.query.filter_by(status='ativo')
                    .options(joinedload(Aluno.instrumento)).order_by(Aluno.nome).all()),
            hoje=format_date_for_form(date.today()),
        )

    @app.route('/adicionar_mensalidade', methods=['POST'])
    @papeis_required('gestora')
    def adicionar_mensalidade():
        try:
            m = Mensalidade(
                aluno_id=int(request.form['aluno_id']),
                competencia=request.form['competencia'],
                valor=float(request.form['valor']),
                vencimento=parse_date(request.form['vencimento']),
            )
            m.atualizar_status()
            db.session.add(m)
            db.session.commit()
            flash('Mensalidade registrada com sucesso!', 'success')
        except (KeyError, ValueError):
            flash('Dados da mensalidade inválidos.', 'danger')
        return redirect(url_for('financeiro'))

    @app.route('/registrar_pagamento/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def registrar_pagamento(id):
        m = db.get_or_404(Mensalidade, id)
        valor = float(request.form.get('valor') or (m.valor - m.total_pago()))
        # Anexar à coleção (e não só gravar por mensalidade_id) garante que
        # atualizar_status() enxergue o pagamento recém-criado.
        m.pagamentos.append(Pagamento(
            valor=valor,
            data_pagamento=parse_date(request.form.get('data_pagamento')) or date.today(),
            observacao=request.form.get('observacao'),
        ))
        m.atualizar_status()
        db.session.commit()
        flash('Pagamento registrado com sucesso!', 'success')
        return redirect(url_for('financeiro'))

    # =================================================================
    # Acompanhamento pedagógico (aulas)
    # =================================================================
    @app.route('/acompanhamento', methods=['GET', 'POST'])
    @papeis_required('gestora', 'professora')
    def acompanhamento():
        escopo = escopo_de_alunos()
        if request.method == 'POST':
            aluno_id = int(request.form['aluno_id'])
            if not pode_ver_aluno(aluno_id, escopo):
                abort(403)
            aula = Aula(
                aluno_id=aluno_id,
                # A professora não escolhe o nome: é o dela que fica gravado, e é
                # esse texto que liga a aula a ela (ver ids_alunos_da_professora).
                professora=(current_user.nome if current_user.is_professora
                            else (request.form.get('professora') or current_user.nome)),
                data=parse_date(request.form.get('data')) or date.today(),
                # Presente/Faltou vira o marcador "Presença: nP/mA" na observação —
                # o mesmo que o importador grava; é o que alimenta os indicadores.
                observacao=marcar_presenca(request.form.get('observacao'),
                                           request.form.get('presenca')),
                orientacao_estudo=request.form.get('orientacao_estudo'),
            )
            db.session.add(aula)
            db.session.commit()
            flash('Acompanhamento pedagógico registrado com sucesso!', 'success')
            return redirect(url_for('acompanhamento'))

        # selectinload: os alunos distintos vêm numa 2ª consulta, em vez de
        # repetir as colunas calculadas do aluno em cada uma das ~1.400 aulas
        consulta_aulas = Aula.query.options(selectinload(Aula.aluno)).order_by(Aula.data.desc())
        consulta_alunos = Aluno.query.filter_by(status='ativo').order_by(Aluno.nome)
        if escopo is not None:                 # professora: só os alunos dela
            consulta_aulas = consulta_aulas.filter(Aula.aluno_id.in_(escopo))
            consulta_alunos = consulta_alunos.filter(Aluno.id.in_(escopo))
        return render_template(
            'acompanhamento.html',
            aulas=consulta_aulas.all(),
            alunos=consulta_alunos.all(),
            hoje=format_date_for_form(date.today()),
            # o formulário de edição mostra o texto sem o marcador de presença
            sem_marcador=sem_marcador,
            contar_presenca=contar_presenca,
        )

    @app.route('/aula/<int:id>/editar', methods=['POST'])
    @papeis_required('gestora', 'professora')
    def editar_aula(id):
        """Edita a observação, a presença e a orientação de estudo de uma aula
        já registrada — o "estudos da semana" que a professora revisa depois."""
        aula = db.get_or_404(Aula, id)
        if not pode_ver_aluno(aula.aluno_id):
            abort(403)
        if 'presenca' in request.form:
            # O formulário sempre manda o campo: vazio ("Não registrar") apaga a
            # marcação de propósito. Sem o campo, preserva a que a aula já tinha.
            presenca = request.form['presenca']
        else:
            presentes, faltas = contar_presenca(aula.observacao)
            presenca = 'presente' if presentes else 'falta' if faltas else ''
        aula.observacao = marcar_presenca(request.form.get('observacao'), presenca)
        aula.orientacao_estudo = request.form.get('orientacao_estudo')
        db.session.commit()
        flash('Acompanhamento atualizado com sucesso!', 'success')
        return redirect(request.form.get('voltar') or url_for('acompanhamento'))

    # =================================================================
    # Área do aluno (consulta apenas dos próprios dados)
    # =================================================================
    @app.route('/minha-area')
    @papeis_required('aluno')
    def minha_area():
        atualizar_status_vencidos()
        aluno = Aluno.query.filter_by(email=current_user.email).first()
        if not aluno:
            flash('Seu usuário ainda não está vinculado a um cadastro de aluno.', 'warning')
            return render_template('minha_area.html', aluno=None, mensalidades=[], aulas=[])
        mensalidades = (Mensalidade.query.filter_by(aluno_id=aluno.id)
                        .order_by(Mensalidade.vencimento.desc()).all())
        aulas = Aula.query.filter_by(aluno_id=aluno.id).order_by(Aula.data.desc()).all()
        return render_template('minha_area.html', aluno=aluno, mensalidades=mensalidades, aulas=aulas)

    # =================================================================
    # Relatórios (gestora) — visão consolidada
    # =================================================================
    @app.route('/relatorios')
    @papeis_required('gestora')
    def relatorios():
        atualizar_status_vencidos()
        alunos = Aluno.query.options(joinedload(Aluno.instrumento)).order_by(Aluno.nome).all()
        pontuar(alunos)
        total_previsto, total_recebido = totais_financeiros()
        return render_template(
            'relatorios.html',
            alunos=alunos,
            total_previsto=total_previsto,
            total_recebido=total_recebido,
            em_aberto=round(total_previsto - total_recebido, 2),
        )

    # =================================================================
    # Handlers de erro
    # =================================================================
    @app.errorhandler(403)
    def acesso_negado(e):
        return render_template('403.html'), 403

    @app.errorhandler(404)
    def pagina_nao_encontrada(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def erro_interno(e):
        db.session.rollback()
        return render_template('500.html'), 500

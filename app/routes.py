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

MENSALIDADES_POR_PAGINA = 50


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

        atualizar_status_vencidos()

        # Os alunos vêm com qtd_em_atraso / valor_em_aberto / ultima_aula já
        # calculados no SELECT (column_property) — risco e inadimplência são
        # aritmética em memória, sem tocar no banco de novo.
        alunos = (Aluno.query.filter_by(status='ativo')
                  .options(joinedload(Aluno.instrumento)).all())
        pontuar(alunos)   # modelo de evasão em lote (3 consultas para todos)

        total_alunos = len(alunos)
        inadimplentes = sum(1 for a in alunos if a.inadimplente)
        adimplentes = total_alunos - inadimplentes
        total_atrasadas = db.session.scalar(
            select(func.count()).select_from(Mensalidade).where(Mensalidade.status == 'em atraso'))

        total_previsto, total_recebido = totais_financeiros()

        # Distribuição de alunos por instrumento (para o gráfico de pizza)
        por_instrumento = db.session.query(
            Instrumento.nome, func.count(Aluno.id)
        ).join(Aluno).group_by(Instrumento.nome).all()
        dados_instrumentos = {
            'nomes': [nome for nome, _ in por_instrumento],
            'valores': [qtd for _, qtd in por_instrumento],
        }

        # Contagem por nível de risco (para o gráfico de barras)
        riscos = {'baixo': 0, 'médio': 0, 'alto': 0}
        for a in alunos:
            riscos[a.risco] += 1

        # Recebido nos últimos 6 meses (para o gráfico de linha) — recebido por
        # mês de referência (competência) da mensalidade, agrupado no banco
        seis_meses = ultimos_meses(6)
        recebido_por_mes = dict(db.session.execute(
            select(Mensalidade.competencia, func.sum(Pagamento.valor))
            .join(Pagamento, Pagamento.mensalidade_id == Mensalidade.id)
            .where(Mensalidade.competencia.in_(seis_meses))
            .group_by(Mensalidade.competencia)
        ).all())
        dados_recebido = {
            'meses': seis_meses,
            'valores': [round(recebido_por_mes.get(ref, 0.0), 2) for ref in seis_meses],
        }

        # Alunos que pedem atenção (risco médio ou alto)
        atencao = sorted(
            (a for a in alunos if a.risco != 'baixo'),
            key=lambda a: a.risco_score, reverse=True,
        )

        return render_template(
            'dashboard.html',
            total_alunos=total_alunos,
            adimplentes=adimplentes,
            inadimplentes=inadimplentes,
            total_atrasadas=total_atrasadas,
            total_previsto=total_previsto,
            total_recebido=total_recebido,
            dados_instrumentos=dados_instrumentos,
            riscos=riscos,
            dados_recebido=dados_recebido,
            alunos_atencao=atencao,
        )

    @app.route('/api/dashboard-data')
    @papeis_required('gestora', 'professora')
    def dashboard_data():
        atualizar_status_vencidos()
        alunos = Aluno.query.filter_by(status='ativo').all()
        pontuar(alunos)
        riscos = {'baixo': 0, 'médio': 0, 'alto': 0}
        for a in alunos:
            riscos[a.risco] += 1
        inadimplentes = sum(1 for a in alunos if a.inadimplente)
        por_instrumento = db.session.query(
            Instrumento.nome, func.count(Aluno.id)
        ).join(Aluno).group_by(Instrumento.nome).all()
        return jsonify({
            'risco': riscos,
            'financeiro': {'Adimplentes': len(alunos) - inadimplentes,
                           'Inadimplentes': inadimplentes},
            'instrumentos': dict(por_instrumento),
        })

    # =================================================================
    # Alunos (equivalente às "transações"/"categorias" do original)
    # =================================================================
    @app.route('/alunos')
    @papeis_required('gestora', 'professora')
    def alunos():
        atualizar_status_vencidos()
        busca = (request.args.get('busca') or '').strip()
        consulta = Aluno.query.options(joinedload(Aluno.instrumento)).order_by(Aluno.nome)
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
        atualizar_status_vencidos()
        aluno = db.get_or_404(Aluno, id)
        # Aluno só acessa a própria ficha
        if current_user.is_aluno:
            vinculo = Aluno.query.filter_by(email=current_user.email).first()
            if not vinculo or vinculo.id != aluno.id:
                abort(403)
        instrumentos = Instrumento.query.order_by(Instrumento.nome).all()
        return render_template('aluno_detalhe.html', aluno=aluno, instrumentos=instrumentos)

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
        if request.method == 'POST':
            aula = Aula(
                aluno_id=int(request.form['aluno_id']),
                professora=request.form.get('professora') or current_user.nome,
                data=parse_date(request.form.get('data')) or date.today(),
                observacao=request.form.get('observacao'),
                orientacao_estudo=request.form.get('orientacao_estudo'),
            )
            db.session.add(aula)
            db.session.commit()
            flash('Acompanhamento pedagógico registrado com sucesso!', 'success')
            return redirect(url_for('acompanhamento'))
        return render_template(
            'acompanhamento.html',
            # selectinload: os alunos distintos vêm numa 2ª consulta, em vez de
            # repetir as colunas calculadas do aluno em cada uma das ~1.400 aulas
            aulas=(Aula.query.options(selectinload(Aula.aluno))
                   .order_by(Aula.data.desc()).all()),
            alunos=Aluno.query.filter_by(status='ativo').order_by(Aluno.nome).all(),
            hoje=format_date_for_form(date.today()),
        )

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

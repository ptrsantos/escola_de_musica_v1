from datetime import datetime, date, timedelta
from functools import wraps

from flask import render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from sqlalchemy import func

from app import db
from app.models import (Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula,
                        format_date_for_form, DIAS_SEMANA_PT,
                        alunos_com_aula_no_dia, aniversariantes_do_dia,
                        aniversariantes_do_mes, balanco_entradas)


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


def parse_time(value):
    """Converte 'HH:MM' (ou 'HH:MM:SS') em datetime.time, ou None se vazio."""
    if not value:
        return None
    value = value.strip()
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def parse_dia_semana(value):
    """Converte o valor do select de dia da semana em int 0..6 ou None.
    Valores vazios ou '-1' (Sem horário fixo) resultam em None."""
    if value is None or value == '':
        return None
    try:
        dia = int(value)
    except (TypeError, ValueError):
        return None
    return dia if 0 <= dia <= 6 else None


def register_routes(app):
    # =================================================================
    # Home / autenticação (mesma estrutura do fluxo de caixa)
    # =================================================================
    @app.route('/')
    def home():
        if current_user.is_authenticated:
            if current_user.is_aluno:
                return redirect(url_for('minha_area'))
            return redirect(url_for('inicio'))
        return render_template('index.html')

    # =================================================================
    # Página inicial operacional (agenda do dia, aniversariantes, entradas)
    # Voltada a quem dá aula: acesso rápido à ficha do aluno para anotar.
    # =================================================================
    @app.route('/inicio')
    @papeis_required('gestora', 'professora')
    def inicio():
        hoje = date.today()
        dia_semana = hoje.weekday()

        agenda_hoje = alunos_com_aula_no_dia(dia_semana)
        aniv_hoje = aniversariantes_do_dia(hoje)
        aniv_mes = aniversariantes_do_mes(hoje.month)

        # Balanço de entradas: pagamentos recebidos hoje e no mês corrente.
        primeiro_dia_mes = hoje.replace(day=1)
        entradas_hoje = balanco_entradas(hoje, hoje)
        entradas_mes = balanco_entradas(primeiro_dia_mes, hoje)

        return render_template(
            'inicio.html',
            hoje=hoje,
            dia_semana_nome=DIAS_SEMANA_PT[dia_semana],
            agenda_hoje=agenda_hoje,
            aniversariantes_hoje=aniv_hoje,
            aniversariantes_mes=aniv_mes,
            entradas_hoje=entradas_hoje,
            entradas_mes=entradas_mes,
        )

    @app.route('/registrar', methods=['GET', 'POST'])
    def registrar():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            nome = request.form.get('nome')
            email = (request.form.get('email') or '').strip().lower()
            senha = request.form.get('senha')
            papel = request.form.get('papel', 'gestora')

            if Usuario.query.filter_by(email=email).first():
                flash('Email já registrado. Faça login.', 'warning')
                return redirect(url_for('login'))

            novo_usuario = Usuario(nome=nome, email=email, papel=papel)
            novo_usuario.set_senha(senha)
            try:
                db.session.add(novo_usuario)
                db.session.commit()
                flash('Conta criada com sucesso! Faça login.', 'success')
                return redirect(url_for('login'))
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao criar conta: {str(e)}', 'danger')
        return render_template('registrar.html')

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
                if next_page:
                    return redirect(next_page)
                if usuario.is_aluno:
                    return redirect(url_for('minha_area'))
                return redirect(url_for('inicio'))
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

        alunos = Aluno.query.filter_by(status='ativo').all()

        # Atualiza status financeiro de todas as mensalidades
        mensalidades = Mensalidade.query.all()
        for m in mensalidades:
            m.atualizar_status()
        db.session.commit()

        total_alunos = len(alunos)
        inadimplentes = sum(1 for a in alunos if a.inadimplente)
        adimplentes = total_alunos - inadimplentes
        total_atrasadas = sum(1 for m in mensalidades if m.status == 'em atraso')

        total_previsto = sum(m.valor for m in mensalidades)
        total_recebido = sum(m.valor_pago for m in mensalidades)

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
        # mês de referência da mensalidade
        seis_meses = []
        hoje = date.today().replace(day=1)
        for i in range(5, -1, -1):
            ref = (hoje - timedelta(days=i * 30)).strftime('%Y-%m')
            seis_meses.append(ref)
        recebido_por_mes = {ref: 0.0 for ref in seis_meses}
        for m in mensalidades:
            if m.competencia in recebido_por_mes:
                recebido_por_mes[m.competencia] += m.valor_pago
        dados_recebido = {
            'meses': seis_meses,
            'valores': [round(recebido_por_mes[ref], 2) for ref in seis_meses],
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
        alunos = Aluno.query.filter_by(status='ativo').all()
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
        busca = (request.args.get('busca') or '').strip()
        consulta = Aluno.query.order_by(Aluno.nome)
        if busca:
            consulta = consulta.filter(Aluno.nome.ilike(f'%{busca}%'))
        return render_template(
            'alunos.html',
            alunos=consulta.all(),
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
                dia_aula_semana=parse_dia_semana(request.form.get('dia_aula_semana')),
                hora_aula=parse_time(request.form.get('hora_aula')),
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
        aluno = db.get_or_404(Aluno, id)
        # Aluno só acessa a própria ficha
        if current_user.is_aluno:
            vinculo = Aluno.query.filter_by(email=current_user.email).first()
            if not vinculo or vinculo.id != aluno.id:
                abort(403)
        for m in aluno.mensalidades:
            m.atualizar_status()
        db.session.commit()
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
            aluno.dia_aula_semana = parse_dia_semana(request.form.get('dia_aula_semana'))
            aluno.hora_aula = parse_time(request.form.get('hora_aula'))
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
        mensalidades = Mensalidade.query.join(Aluno).order_by(Mensalidade.vencimento.desc()).all()
        for m in mensalidades:
            m.atualizar_status()
        db.session.commit()
        return render_template(
            'financeiro.html',
            mensalidades=mensalidades,
            alunos=Aluno.query.filter_by(status='ativo').order_by(Aluno.nome).all(),
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

    @app.route('/editar_mensalidade/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def editar_mensalidade(id):
        m = db.get_or_404(Mensalidade, id)
        try:
            m.aluno_id = int(request.form['aluno_id'])
            m.competencia = request.form['competencia']
            m.valor = float(request.form['valor'])
            m.vencimento = parse_date(request.form['vencimento'])
            m.atualizar_status()
            db.session.commit()
            flash('Mensalidade atualizada com sucesso!', 'success')
        except (KeyError, ValueError):
            db.session.rollback()
            flash('Dados da mensalidade inválidos.', 'danger')
        return redirect(url_for('financeiro'))

    @app.route('/excluir_mensalidade/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def excluir_mensalidade(id):
        m = db.get_or_404(Mensalidade, id)
        db.session.delete(m)  # remove os pagamentos em cascata
        db.session.commit()
        flash('Mensalidade removida com sucesso!', 'success')
        return redirect(url_for('financeiro'))

    @app.route('/registrar_pagamento/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def registrar_pagamento(id):
        m = db.get_or_404(Mensalidade, id)
        valor = float(request.form.get('valor') or (m.valor - m.valor_pago))
        db.session.add(Pagamento(
            mensalidade_id=m.id,
            valor=valor,
            data_pagamento=parse_date(request.form.get('data_pagamento')) or date.today(),
            observacao=request.form.get('observacao'),
        ))
        m.atualizar_status()
        db.session.commit()
        flash('Pagamento registrado com sucesso!', 'success')
        return redirect(url_for('financeiro'))

    @app.route('/editar_pagamento/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def editar_pagamento(id):
        p = db.get_or_404(Pagamento, id)
        try:
            p.valor = float(request.form['valor'])
            p.data_pagamento = parse_date(request.form.get('data_pagamento')) or p.data_pagamento
            p.observacao = request.form.get('observacao')
            p.mensalidade.atualizar_status()
            db.session.commit()
            flash('Pagamento atualizado com sucesso!', 'success')
        except (KeyError, ValueError):
            db.session.rollback()
            flash('Dados do pagamento inválidos.', 'danger')
        return redirect(url_for('financeiro'))

    @app.route('/excluir_pagamento/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def excluir_pagamento(id):
        p = db.get_or_404(Pagamento, id)
        mensalidade = p.mensalidade
        db.session.delete(p)
        db.session.flush()
        mensalidade.atualizar_status()
        db.session.commit()
        flash('Pagamento removido com sucesso!', 'success')
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
            aulas=Aula.query.join(Aluno).order_by(Aula.data.desc()).all(),
            alunos=Aluno.query.filter_by(status='ativo').order_by(Aluno.nome).all(),
            hoje=format_date_for_form(date.today()),
        )

    @app.route('/editar_aula/<int:id>', methods=['POST'])
    @papeis_required('gestora', 'professora')
    def editar_aula(id):
        aula = db.get_or_404(Aula, id)
        try:
            aula.aluno_id = int(request.form['aluno_id'])
            aula.professora = request.form.get('professora') or aula.professora
            aula.data = parse_date(request.form.get('data')) or aula.data
            aula.observacao = request.form.get('observacao')
            aula.orientacao_estudo = request.form.get('orientacao_estudo')
            db.session.commit()
            flash('Acompanhamento atualizado com sucesso!', 'success')
        except (KeyError, ValueError):
            db.session.rollback()
            flash('Dados do acompanhamento inválidos.', 'danger')
        return redirect(request.form.get('next') or url_for('acompanhamento'))

    @app.route('/excluir_aula/<int:id>', methods=['POST'])
    @papeis_required('gestora', 'professora')
    def excluir_aula(id):
        aula = db.get_or_404(Aula, id)
        destino = request.form.get('next') or url_for('acompanhamento')
        db.session.delete(aula)
        db.session.commit()
        flash('Acompanhamento removido com sucesso!', 'success')
        return redirect(destino)

    # =================================================================
    # Área do aluno (consulta apenas dos próprios dados)
    # =================================================================
    @app.route('/minha-area')
    @papeis_required('aluno')
    def minha_area():
        aluno = Aluno.query.filter_by(email=current_user.email).first()
        if not aluno:
            flash('Seu usuário ainda não está vinculado a um cadastro de aluno.', 'warning')
            return render_template('minha_area.html', aluno=None, mensalidades=[], aulas=[])
        mensalidades = (Mensalidade.query.filter_by(aluno_id=aluno.id)
                        .order_by(Mensalidade.vencimento.desc()).all())
        for m in mensalidades:
            m.atualizar_status()
        db.session.commit()
        aulas = Aula.query.filter_by(aluno_id=aluno.id).order_by(Aula.data.desc()).all()
        return render_template('minha_area.html', aluno=aluno, mensalidades=mensalidades, aulas=aulas)

    # =================================================================
    # Relatórios (gestora) — visão consolidada
    # =================================================================
    @app.route('/relatorios')
    @papeis_required('gestora')
    def relatorios():
        alunos = Aluno.query.order_by(Aluno.nome).all()
        mensalidades = Mensalidade.query.all()
        for m in mensalidades:
            m.atualizar_status()
        db.session.commit()
        total_previsto = sum(m.valor for m in mensalidades)
        total_recebido = sum(m.valor_pago for m in mensalidades)
        return render_template(
            'relatorios.html',
            alunos=alunos,
            total_previsto=total_previsto,
            total_recebido=total_recebido,
            em_aberto=round(total_previsto - total_recebido, 2),
        )

    # =================================================================
    # Instrumentos (cadastro) — somente gestora
    # =================================================================
    @app.route('/instrumentos')
    @papeis_required('gestora')
    def instrumentos():
        lista = Instrumento.query.order_by(Instrumento.nome).all()
        return render_template('instrumentos.html', instrumentos=lista)

    @app.route('/adicionar_instrumento', methods=['POST'])
    @papeis_required('gestora')
    def adicionar_instrumento():
        nome = (request.form.get('nome') or '').strip()
        if not nome:
            flash('Informe o nome do instrumento.', 'danger')
        elif Instrumento.query.filter(func.lower(Instrumento.nome) == nome.lower()).first():
            flash('Já existe um instrumento com esse nome.', 'warning')
        else:
            db.session.add(Instrumento(nome=nome))
            db.session.commit()
            flash('Instrumento adicionado com sucesso!', 'success')
        return redirect(url_for('instrumentos'))

    @app.route('/editar_instrumento/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def editar_instrumento(id):
        instrumento = db.get_or_404(Instrumento, id)
        nome = (request.form.get('nome') or '').strip()
        if not nome:
            flash('Informe o nome do instrumento.', 'danger')
        elif Instrumento.query.filter(func.lower(Instrumento.nome) == nome.lower(),
                                      Instrumento.id != id).first():
            flash('Já existe um instrumento com esse nome.', 'warning')
        else:
            instrumento.nome = nome
            db.session.commit()
            flash('Instrumento atualizado com sucesso!', 'success')
        return redirect(url_for('instrumentos'))

    @app.route('/excluir_instrumento/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def excluir_instrumento(id):
        instrumento = db.get_or_404(Instrumento, id)
        if instrumento.alunos:
            flash(f'Não é possível excluir "{instrumento.nome}": há {len(instrumento.alunos)} '
                  f'aluno(s) vinculado(s).', 'danger')
        else:
            db.session.delete(instrumento)
            db.session.commit()
            flash('Instrumento removido com sucesso!', 'success')
        return redirect(url_for('instrumentos'))

    # =================================================================
    # Usuários (gestão de acesso) — somente gestora
    # =================================================================
    @app.route('/usuarios')
    @papeis_required('gestora')
    def usuarios():
        lista = Usuario.query.order_by(Usuario.nome).all()
        return render_template('usuarios.html', usuarios=lista,
                               papeis=[Usuario.PAPEL_GESTORA, Usuario.PAPEL_PROFESSORA,
                                       Usuario.PAPEL_ALUNO])

    @app.route('/adicionar_usuario', methods=['POST'])
    @papeis_required('gestora')
    def adicionar_usuario():
        nome = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha')
        papel = request.form.get('papel', Usuario.PAPEL_GESTORA)
        if not (nome and email and senha):
            flash('Preencha nome, e-mail e senha.', 'danger')
        elif Usuario.query.filter_by(email=email).first():
            flash('Já existe um usuário com esse e-mail.', 'warning')
        else:
            novo = Usuario(nome=nome, email=email, papel=papel)
            novo.set_senha(senha)
            db.session.add(novo)
            db.session.commit()
            flash('Usuário criado com sucesso!', 'success')
        return redirect(url_for('usuarios'))

    @app.route('/editar_usuario/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def editar_usuario(id):
        usuario = db.get_or_404(Usuario, id)
        nome = (request.form.get('nome') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        papel = request.form.get('papel', usuario.papel)
        senha = request.form.get('senha')
        conflito = Usuario.query.filter(Usuario.email == email, Usuario.id != id).first()
        if not (nome and email):
            flash('Nome e e-mail são obrigatórios.', 'danger')
        elif conflito:
            flash('Já existe outro usuário com esse e-mail.', 'warning')
        else:
            usuario.nome = nome
            usuario.email = email
            usuario.papel = papel
            if senha:
                usuario.set_senha(senha)
            db.session.commit()
            flash('Usuário atualizado com sucesso!', 'success')
        return redirect(url_for('usuarios'))

    @app.route('/excluir_usuario/<int:id>', methods=['POST'])
    @papeis_required('gestora')
    def excluir_usuario(id):
        usuario = db.get_or_404(Usuario, id)
        if usuario.id == current_user.id:
            flash('Você não pode excluir o próprio usuário conectado.', 'danger')
        else:
            db.session.delete(usuario)
            db.session.commit()
            flash('Usuário removido com sucesso!', 'success')
        return redirect(url_for('usuarios'))

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

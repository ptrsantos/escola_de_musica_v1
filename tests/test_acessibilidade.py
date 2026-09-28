"""
Testes estruturais de acessibilidade (WCAG 2.2 AA) sobre o HTML renderizado.

O que estes testes fazem: percorrem as páginas por perfil e verificam a
**presença** da estrutura exigida pela skill `acessibilidade-web` — landmarks,
rótulo associado a cada campo, nome acessível em botões de ícone, identificação
dos modais, legenda nas tabelas e alternativa textual nos gráficos.

O que estes testes NÃO fazem: avaliar a *qualidade* dos nomes acessíveis, a
ordem de foco, o contraste real após a cascata do CSS, o comportamento com zoom
ou a experiência com leitor de tela. Isso exige verificação manual — ver
docs/acessibilidade.md. A cobertura automatizada de acessibilidade fica em torno
de 30% dos critérios da WCAG; estes testes servem para impedir regressão, não
para atestar conformidade.
"""
import pytest
from bs4 import BeautifulSoup

from conftest import logar_gestora, logar_professora, logar_aluno

# Páginas completas (herdam base.html) acessíveis a cada perfil.
PAGINAS_GESTORA = ['/inicio', '/dashboard', '/alunos', '/financeiro', '/relatorios',
                   '/acompanhamento', '/instrumentos', '/usuarios', '/registrar']
PAGINAS_PROFESSORA = ['/inicio', '/dashboard', '/alunos', '/acompanhamento']
PAGINAS_ANONIMAS = ['/', '/login', '/alterar-senha']

# Tipos de input que não precisam de rótulo visível para o usuário.
TIPOS_SEM_ROTULO = {'hidden', 'submit', 'button', 'reset', 'image', 'csrf_token'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sopa(client, url):
    """Faz GET em ``url``, exige 200 e devolve a árvore HTML analisada."""
    resposta = client.get(url)
    assert resposta.status_code == 200, f'{url} devolveu {resposta.status_code}'
    return BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')


def paginas(client, rotas):
    """Gera (rota, árvore) para cada rota, já autenticado pelo chamador."""
    for rota in rotas:
        yield rota, sopa(client, rota)


def tem_nome_acessivel(elemento):
    """Nome acessível por texto visível, aria-label ou aria-labelledby.

    ``title`` não conta: não é anunciado de forma consistente e não aparece em
    dispositivos de toque.
    """
    if elemento.get_text(strip=True):
        return True
    if elemento.get('aria-label') or elemento.get('aria-labelledby'):
        return True
    # Botão cujo conteúdo é um <img> com alt preenchido.
    return any(img.get('alt') for img in elemento.find_all('img'))


def campos_visiveis(arvore):
    """Campos de formulário que precisam de rótulo associado."""
    for campo in arvore.select('input, select, textarea'):
        if campo.name == 'input' and (campo.get('type') or 'text') in TIPOS_SEM_ROTULO:
            continue
        yield campo


def ids_rotulados(arvore):
    return {rotulo.get('for') for rotulo in arvore.find_all('label') if rotulo.get('for')}


# ---------------------------------------------------------------------------
# Requisito 1 — estrutura semântica e landmarks
# ---------------------------------------------------------------------------
def verificar_landmarks(arvore, rota):
    principais = arvore.find_all('main')
    assert len(principais) == 1, f'{rota}: esperado 1 <main>, encontrado {len(principais)}'
    assert principais[0].get('id') == 'conteudo-principal', \
        f'{rota}: <main> sem id="conteudo-principal"'

    atalho = arvore.select_one('a.skip-link')
    assert atalho is not None, f'{rota}: sem link "Pular para o conteúdo"'
    assert atalho.get('href') == '#conteudo-principal', \
        f'{rota}: skip link não aponta para #conteudo-principal'

    titulos = arvore.find_all('h1')
    assert len(titulos) == 1, f'{rota}: esperado 1 <h1>, encontrado {len(titulos)}'
    assert titulos[0].get_text(strip=True), f'{rota}: <h1> vazio'


@pytest.mark.parametrize('rota', PAGINAS_GESTORA)
def test_landmarks_gestora(dados, client, rota):
    logar_gestora(client)
    verificar_landmarks(sopa(client, rota), rota)


@pytest.mark.parametrize('rota', PAGINAS_PROFESSORA)
def test_landmarks_professora(dados, client, rota):
    logar_professora(client)
    verificar_landmarks(sopa(client, rota), rota)


@pytest.mark.parametrize('rota', PAGINAS_ANONIMAS)
def test_landmarks_anonimo(dados, client, rota):
    verificar_landmarks(sopa(client, rota), rota)


def test_landmarks_ficha_do_aluno(dados, client):
    logar_gestora(client)
    rota = f'/aluno/{dados["bruno"]}'
    verificar_landmarks(sopa(client, rota), rota)


def test_landmarks_area_do_aluno(dados, client):
    logar_aluno(client)
    verificar_landmarks(sopa(client, '/minha-area'), '/minha-area')


def test_skip_link_e_o_primeiro_elemento_focavel(dados, client):
    """O atalho tem de vir antes de qualquer outro elemento focável do corpo e
    levar a um destino que aceite foco de programação (T4)."""
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    focaveis = arvore.body.select('a[href], button, input:not([type="hidden"]), select, textarea')
    assert focaveis, 'nenhum elemento focável encontrado'
    skip = focaveis[0]
    assert 'skip-link' in (skip.get('class') or []), \
        f'primeiro focável é {skip.name}, não o skip link'
    alvo_id = skip.get('href', '').lstrip('#')
    alvo = arvore.find(id=alvo_id)
    assert alvo is not None, f'skip link aponta para #{alvo_id}, que não existe'
    assert alvo.get('tabindex') == '-1', \
        'destino do skip link sem tabindex="-1": o foco não se move ao clicar'


def test_navegacoes_tem_rotulos_distintos(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    navs = arvore.find_all('nav')
    assert len(navs) >= 2, 'esperadas ao menos a navegação superior e a lateral'
    rotulos = [nav.get('aria-label') for nav in navs]
    assert all(rotulos), 'há <nav> sem aria-label'
    assert len(set(rotulos)) == len(rotulos), f'<nav> com aria-label repetido: {rotulos}'


def test_item_de_navegacao_atual_marcado(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    atuais = arvore.select('[aria-current="page"]')
    assert atuais, '/alunos: nenhum item de navegação marcado com aria-current="page"'


def test_hierarquia_de_titulos_sem_saltos(dados, client):
    logar_gestora(client)
    for rota, arvore in paginas(client, PAGINAS_GESTORA):
        niveis = [int(t.name[1]) for t in arvore.select('h1, h2, h3, h4, h5, h6')]
        assert niveis[0] == 1, f'{rota}: primeiro título é h{niveis[0]}, não h1'
        for anterior, atual in zip(niveis, niveis[1:]):
            assert atual <= anterior + 1, \
                f'{rota}: salto de h{anterior} para h{atual}'


# ---------------------------------------------------------------------------
# Requisito 1 — HTML bem-formado (regressão: aspas escapadas literais)
# ---------------------------------------------------------------------------
# O código herdado do kiro renderizou `<i class=\"fas ...\">` em 31 linhas de
# 11 templates: o HTML quebra, ícones somem e atributos viram valores com
# barra. Esta suíte falha se a sequência abaixo reaparecer em página
# renderizada.
ASPAS_ESCAPADAS = '\\"'


def verificar_html_sem_aspas_escapadas(client, rota):
    html = client.get(rota).get_data(as_text=True)
    assert ASPAS_ESCAPADAS not in html, \
        f'{rota}: HTML renderizado contém {ASPAS_ESCAPADAS} (atributo quebrado)'


@pytest.mark.parametrize('rota', PAGINAS_GESTORA)
def test_html_sem_aspas_escapadas_gestora(dados, client, rota):
    logar_gestora(client)
    verificar_html_sem_aspas_escapadas(client, rota)


@pytest.mark.parametrize('rota', PAGINAS_PROFESSORA)
def test_html_sem_aspas_escapadas_professora(dados, client, rota):
    logar_professora(client)
    verificar_html_sem_aspas_escapadas(client, rota)


@pytest.mark.parametrize('rota', PAGINAS_ANONIMAS)
def test_html_sem_aspas_escapadas_anonimo(dados, client, rota):
    verificar_html_sem_aspas_escapadas(client, rota)


def test_html_sem_aspas_escapadas_ficha_do_aluno(dados, client):
    logar_gestora(client)
    verificar_html_sem_aspas_escapadas(client, f'/aluno/{dados["bruno"]}')


def test_html_sem_aspas_escapadas_area_do_aluno(dados, client):
    logar_aluno(client)
    verificar_html_sem_aspas_escapadas(client, '/minha-area')


# ---------------------------------------------------------------------------
# Requisito 5 — controles que expandem ou colapsam regiões
# ---------------------------------------------------------------------------
def test_controles_de_menu_declaram_estado_e_regiao(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')

    for id_botao, id_regiao in [('sidebar-toggle', 'sidebar'), (None, 'navbarNav')]:
        if id_botao:
            botao = arvore.find(id=id_botao)
        else:
            botao = arvore.select_one('.navbar-toggler')
        assert botao is not None, f'controle de {id_regiao} não encontrado'
        assert botao.get('aria-expanded') in ('true', 'false'), \
            f'{id_regiao}: controle sem aria-expanded'
        assert botao.get('aria-controls') == id_regiao, \
            f'{id_regiao}: controle sem aria-controls correto'
        assert tem_nome_acessivel(botao), f'{id_regiao}: controle sem nome acessível'
        assert arvore.find(id=id_regiao) is not None, f'região {id_regiao} não existe'


def test_menu_do_usuario_e_botao_e_nao_link_vazio(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    alternador = arvore.find(id='navbarDropdown')
    assert alternador is not None and alternador.name == 'button', \
        'o menu do usuário deve ser <button>, não link com href="#"'
    assert alternador.get('aria-expanded') in ('true', 'false')


def test_nenhum_link_de_acao_com_href_vazio(dados, client):
    """href="#" em link de ação confunde navegação por teclado e leitor de tela.

    A paginação do dashboard é construída em JavaScript e é verificada à parte.
    """
    logar_gestora(client)
    for rota, arvore in paginas(client, PAGINAS_GESTORA):
        vazios = [a for a in arvore.find_all('a') if a.get('href') == '#']
        assert not vazios, f'{rota}: {len(vazios)} link(s) com href="#"'


# ---------------------------------------------------------------------------
# Requisito 2 — rótulos de formulário associados
# ---------------------------------------------------------------------------
def verificar_rotulos(arvore, rota):
    rotulados = ids_rotulados(arvore)
    for campo in campos_visiveis(arvore):
        id_campo = campo.get('id')
        tem_rotulo = (id_campo in rotulados
                      or campo.get('aria-label')
                      or campo.get('aria-labelledby'))
        assert tem_rotulo, (
            f'{rota}: campo sem rótulo associado '
            f'(name={campo.get("name")!r}, id={id_campo!r})')


@pytest.mark.parametrize('rota', PAGINAS_GESTORA)
def test_rotulos_gestora(dados, client, rota):
    logar_gestora(client)
    verificar_rotulos(sopa(client, rota), rota)


@pytest.mark.parametrize('rota', PAGINAS_ANONIMAS)
def test_rotulos_anonimo(dados, client, rota):
    verificar_rotulos(sopa(client, rota), rota)


def test_rotulos_ficha_do_aluno(dados, client):
    logar_gestora(client)
    rota = f'/aluno/{dados["ana"]}'
    verificar_rotulos(sopa(client, rota), rota)


def test_ids_sao_unicos_no_documento(dados, client):
    """id duplicado quebra a associação label/for e o aria-describedby."""
    logar_gestora(client)
    for rota, arvore in paginas(client, PAGINAS_GESTORA):
        ids = [e['id'] for e in arvore.select('[id]')]
        repetidos = {i for i in ids if ids.count(i) > 1}
        assert not repetidos, f'{rota}: id(s) duplicado(s): {sorted(repetidos)}'


def test_nomes_de_campo_nao_se_repetem_no_mesmo_formulario(dados, client):
    logar_gestora(client)
    for rota, arvore in paginas(client, PAGINAS_GESTORA):
        for indice, formulario in enumerate(arvore.find_all('form')):
            nomes = [c.get('name') for c in formulario.select('input, select, textarea')
                     if c.get('name') and c.get('type') != 'hidden']
            repetidos = {n for n in nomes if nomes.count(n) > 1}
            assert not repetidos, \
                f'{rota}: formulário {indice} repete o campo {sorted(repetidos)}'


def test_campo_obrigatorio_tem_indicacao_textual(dados, client):
    """O asterisco é visual; precisa de equivalente em texto."""
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    rotulos = {r.get('for'): r for r in arvore.find_all('label') if r.get('for')}
    obrigatorios = [c for c in campos_visiveis(arvore) if c.has_attr('required')]
    assert obrigatorios, '/alunos: nenhum campo obrigatório encontrado'
    for campo in obrigatorios:
        rotulo = rotulos.get(campo.get('id'))
        assert rotulo is not None, f'campo obrigatório sem rótulo: {campo.get("name")}'
        assert 'obrigatório' in rotulo.get_text().lower(), \
            f'campo obrigatório {campo.get("name")!r} sem indicação textual'


# ---------------------------------------------------------------------------
# Requisito 3 — erros de formulário perceptíveis
# ---------------------------------------------------------------------------
def test_erro_de_cadastro_de_aluno_fica_associado_ao_campo(dados, client):
    logar_gestora(client)
    resposta = client.post('/adicionar_aluno',
                           data={'nome': 'ab', 'instrumento_id': '', 'mensalidade_base': 'abc'})
    assert resposta.status_code == 200, 'erro de validação não deve redirecionar'
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')

    resumo = arvore.select_one('.alert[role="alert"][tabindex="-1"]')
    assert resumo is not None, 'sem resumo de erros com role="alert"'
    assert resumo.find('a', href='#campo-nome') is not None, \
        'o resumo não tem link para o campo inválido'

    invalido = arvore.find(id='campo-nome')
    assert invalido.get('aria-invalid') == 'true', 'campo inválido sem aria-invalid'
    descritores = (invalido.get('aria-describedby') or '').split()
    assert 'campo-nome-erro' in descritores, 'mensagem de erro não associada ao campo'
    assert arvore.find(id='campo-nome-erro').get_text(strip=True), 'mensagem de erro vazia'

    # O modal volta marcado para reabrir, senão o formulário fica invisível.
    assert arvore.body.get('data-abrir-modal') == 'novoAlunoModal'


def test_valores_digitados_sao_preservados_no_erro(dados, client):
    logar_gestora(client)
    resposta = client.post('/adicionar_aluno', data={
        'nome': 'ab', 'instrumento_id': '', 'endereco': 'Rua das Flores, 42',
        'email': 'sem-arroba'})
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    assert arvore.find(id='campo-nome').get('value') == 'ab'
    assert arvore.find(id='campo-endereco').get('value') == 'Rua das Flores, 42'
    assert arvore.find(id='campo-email').get('value') == 'sem-arroba'
    assert arvore.find(id='campo-email').get('aria-invalid') == 'true'


def test_cadastro_de_aluno_valido_continua_redirecionando(dados, client):
    logar_gestora(client)
    resposta = client.post('/adicionar_aluno',
                           data={'nome': 'Novo Aluno', 'instrumento_id': 1,
                                 'mensalidade_base': '150'})
    assert resposta.status_code == 302 and '/aluno/' in resposta.headers['Location']


def test_erro_de_usuario_preserva_valores_e_reabre_o_modal(dados, client):
    logar_gestora(client)
    resposta = client.post('/adicionar_usuario',
                           data={'nome': 'Sem Senha', 'email': 'novo@escola.com', 'senha': ''})
    assert resposta.status_code == 200
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    assert arvore.body.get('data-abrir-modal') == 'novoUsuarioModal'
    assert arvore.find(id='novo-u-nome').get('value') == 'Sem Senha'
    assert arvore.find(id='novo-u-senha').get('aria-invalid') == 'true'


def test_email_duplicado_aponta_o_campo(dados, client):
    logar_gestora(client)
    resposta = client.post('/adicionar_usuario', data={
        'nome': 'Outra Gestora', 'email': 'gestora@escola.com', 'senha': '123456'})
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    assert arvore.find(id='novo-u-email').get('aria-invalid') == 'true'
    assert 'e-mail' in arvore.find(id='novo-u-email-erro').get_text().lower()


# ---------------------------------------------------------------------------
# Requisito 6 — modais identificáveis
# ---------------------------------------------------------------------------
PAGINAS_COM_MODAL = ['/alunos', '/financeiro', '/acompanhamento', '/instrumentos', '/usuarios']


@pytest.mark.parametrize('rota', PAGINAS_COM_MODAL)
def test_modais_tem_nome_acessivel(dados, client, rota):
    logar_gestora(client)
    arvore = sopa(client, rota)
    modais = arvore.select('.modal')
    assert modais, f'{rota}: nenhum modal encontrado'
    for modal in modais:
        id_modal = modal.get('id')
        rotulo = modal.get('aria-labelledby')
        assert rotulo, f'{rota}: modal {id_modal} sem aria-labelledby'
        titulo = arvore.find(id=rotulo)
        assert titulo is not None, f'{rota}: modal {id_modal} aponta para id inexistente {rotulo}'
        assert titulo.get_text(strip=True), f'{rota}: título do modal {id_modal} está vazio'


@pytest.mark.parametrize('rota', PAGINAS_COM_MODAL)
def test_botoes_de_fechar_modal_tem_nome(dados, client, rota):
    logar_gestora(client)
    arvore = sopa(client, rota)
    fechar = arvore.select('.modal .btn-close')
    assert fechar, f'{rota}: nenhum botão de fechar encontrado'
    for botao in fechar:
        assert botao.get('aria-label'), f'{rota}: .btn-close sem aria-label'


def test_modal_de_exclusao_descreve_a_consequencia(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    modal = arvore.find(id='deleteModal')
    descricao = modal.get('aria-describedby')
    assert descricao, 'modal de exclusão sem aria-describedby'
    aviso = arvore.find(id=descricao)
    assert aviso is not None and 'não pode ser desfeita' in aviso.get_text()


# ---------------------------------------------------------------------------
# Requisito 4 — nomes acessíveis em controles
# ---------------------------------------------------------------------------
def verificar_nomes_de_controles(arvore, rota):
    for botao in arvore.find_all('button'):
        assert tem_nome_acessivel(botao), \
            f'{rota}: botão sem nome acessível: {str(botao)[:120]}'
    for link in arvore.find_all('a'):
        assert tem_nome_acessivel(link), \
            f'{rota}: link sem nome acessível: {str(link)[:120]}'


@pytest.mark.parametrize('rota', PAGINAS_GESTORA)
def test_controles_tem_nome_gestora(dados, client, rota):
    logar_gestora(client)
    verificar_nomes_de_controles(sopa(client, rota), rota)


@pytest.mark.parametrize('rota', PAGINAS_PROFESSORA)
def test_controles_tem_nome_professora(dados, client, rota):
    logar_professora(client)
    verificar_nomes_de_controles(sopa(client, rota), rota)


def test_controles_tem_nome_ficha_e_area_do_aluno(dados, client):
    logar_gestora(client)
    rota = f'/aluno/{dados["bruno"]}'
    verificar_nomes_de_controles(sopa(client, rota), rota)


def test_nome_do_controle_identifica_o_registro(dados, client):
    """Numa lista, "Excluir" sozinho não diz o que será excluído."""
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    rotulos = [b.get('aria-label') for b in arvore.select('tbody button[aria-label]')]
    assert any('Bruno Costa' in (r or '') for r in rotulos), \
        f'nenhum controle da lista cita o aluno: {rotulos}'


def test_icones_decorativos_estao_ocultos(dados, client):
    """Ícone de fonte sem texto vira ruído no leitor de tela.

    Só são exigidos aria-hidden os ícones que não carregam o nome do controle:
    quando o pai já tem aria-label, o ícone é ignorado de qualquer forma.
    """
    logar_gestora(client)
    for rota, arvore in paginas(client, PAGINAS_GESTORA):
        expostos = []
        for icone in arvore.select('i.fas, i.far, i.fab, i.bi'):
            if icone.get('aria-hidden') == 'true':
                continue
            pai = icone.parent
            if pai is not None and (pai.get('aria-label') or pai.get('aria-hidden')):
                continue
            expostos.append(str(icone)[:80])
        assert not expostos, f'{rota}: ícone(s) decorativo(s) sem aria-hidden: {expostos}'


# ---------------------------------------------------------------------------
# Requisito 7 — tabelas de dados
# ---------------------------------------------------------------------------
def verificar_tabelas(arvore, rota):
    for indice, tabela in enumerate(arvore.find_all('table')):
        legenda = tabela.find('caption')
        assert legenda is not None and legenda.get_text(strip=True), \
            f'{rota}: tabela {indice} sem <caption> descritiva'
        cabecalhos = tabela.select('thead th')
        assert cabecalhos, f'{rota}: tabela {indice} sem cabeçalhos de coluna'
        for th in cabecalhos:
            assert th.get('scope') == 'col', \
                f'{rota}: tabela {indice} tem <th> de coluna sem scope="col"'
        for th in tabela.select('tbody th'):
            assert th.get('scope') == 'row', \
                f'{rota}: tabela {indice} tem <th> de linha sem scope="row"'


@pytest.mark.parametrize('rota', PAGINAS_GESTORA)
def test_tabelas_gestora(dados, client, rota):
    logar_gestora(client)
    verificar_tabelas(sopa(client, rota), rota)


def test_tabelas_ficha_e_area_do_aluno(dados, client):
    logar_gestora(client)
    rota = f'/aluno/{dados["ana"]}'
    verificar_tabelas(sopa(client, rota), rota)
    # /login redireciona quem já está autenticado: encerra a sessão antes de
    # entrar com o outro perfil.
    client.get('/logout')
    logar_aluno(client)
    verificar_tabelas(sopa(client, '/minha-area'), '/minha-area')


def test_linhas_das_listas_tem_cabecalho_de_linha(dados, client):
    """O nome do aluno identifica a linha: como <th scope="row">, o leitor de
    tela o repete ao navegar pelas células."""
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    cabecalhos = [th.get_text(strip=True) for th in arvore.select('tbody th[scope="row"]')]
    assert any('Bruno Costa' in c for c in cabecalhos), \
        'a linha do aluno não usa <th scope="row">'


def test_grade_de_ocupacao_tem_cabecalhos_nos_dois_eixos(app):
    """Grade dia x hora: hora é cabeçalho de linha, dia é cabeçalho de coluna."""
    from datetime import time
    from app import db
    from app.models import Aluno
    from conftest import popular_dados
    ids = popular_dados(app)
    with app.app_context():
        aluno = db.session.get(Aluno, ids['ana'])
        aluno.dia_aula_semana = 1
        aluno.hora_aula = time(14, 0)
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    grade = arvore.find(id='tabelaOcupacao')
    assert grade is not None, 'grade de ocupação não renderizada'
    assert [th.get_text(strip=True) for th in grade.select('tbody th[scope="row"]')] == ['14:00']
    assert 'Terça' in [th.get_text(strip=True) for th in grade.select('thead th[scope="col"]')]


# ---------------------------------------------------------------------------
# Requisito 8 — gráficos com equivalente textual
# ---------------------------------------------------------------------------
def test_graficos_tem_nome_e_tabela_equivalente(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    canvas = arvore.find_all('canvas')

    # Os gráficos de instrumento e de presença só são renderizados quando há
    # dados; os outros quatro estão sempre presentes.
    ids = {c.get('id') for c in canvas}
    assert {'financeiroChart', 'recebidoChart', 'riscoChart', 'diaSemanaChart'} <= ids, \
        f'gráficos ausentes: {ids}'

    for grafico in canvas:
        id_grafico = grafico.get('id')
        assert grafico.get('role') == 'img', f'{id_grafico}: canvas sem role="img"'
        rotulo = grafico.get('aria-label') or ''
        assert len(rotulo) > 30, f'{id_grafico}: aria-label ausente ou vago'

        figura = grafico.find_parent('figure')
        assert figura is not None, f'{id_grafico}: canvas fora de <figure>'
        detalhes = figura.select_one('figcaption details')
        assert detalhes is not None, f'{id_grafico}: sem <details> com os dados'
        assert detalhes.find('summary').get_text(strip=True), f'{id_grafico}: <summary> vazio'
        tabela = detalhes.find('table')
        assert tabela is not None, f'{id_grafico}: <details> sem tabela equivalente'
        assert tabela.select('tbody tr'), f'{id_grafico}: tabela equivalente vazia'


def test_tabela_do_grafico_traz_os_mesmos_numeros(dados, client):
    """Conjunto ``dados``: 1 adimplente (Ana) e 2 inadimplentes (Bruno, Carla);
    risco baixo 1, médio 1, alto 1."""
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')

    financeiro = arvore.find(id='financeiroChart').find_parent('figure')
    linhas = {th.get_text(strip=True): th.find_next('td').get_text(strip=True)
              for th in financeiro.select('tbody th')}
    assert linhas == {'Adimplentes': '1', 'Inadimplentes': '2'}

    risco = arvore.find(id='riscoChart').find_parent('figure')
    linhas = {th.get_text(strip=True): th.find_next('td').get_text(strip=True)
              for th in risco.select('tbody th')}
    assert linhas == {'Baixo': '1', 'Médio': '1', 'Alto': '1'}


def test_grafico_sem_dados_informa_em_html(app):
    """Sem dados, a informação precisa estar no HTML, não desenhada no canvas."""
    from app import db
    from app.models import Usuario
    from conftest import novo_usuario
    with app.app_context():
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    assert arvore.find(id='instrumentoChart') is None, \
        'canvas renderizado sem dados para mostrar'
    assert 'Nenhum aluno vinculado a instrumentos ainda.' in arvore.get_text()
    assert 'fillText' not in str(arvore), 'texto desenhado no canvas é inacessível'


def test_grafico_de_presenca_tambem_tem_tabela(dados, app, client):
    """O gráfico de presença só existe quando há marcador ``Presença: nP/mA``."""
    from app import db
    from app.models import Aula
    with app.app_context():
        aula = Aula.query.first()
        aula.observacao = 'Presença: 3P/1A · escalas'
        db.session.commit()
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    grafico = arvore.find(id='presencaChart')
    assert grafico is not None, 'gráfico de presença não renderizado com dados disponíveis'
    assert grafico.get('role') == 'img' and grafico.get('aria-label')
    tabela = grafico.find_parent('figure').select_one('figcaption details table')
    assert tabela is not None and tabela.select('tbody tr'), \
        'gráfico de presença sem tabela equivalente'
    assert '75%' in tabela.get_text(), 'a tabela não traz a taxa de presença calculada'


def test_graficos_tem_descricao_de_tendencia(dados, client):
    """REQ 4/T6: cada canvas é descrito por texto de tendência gerado no
    servidor a partir dos mesmos dados do gráfico."""
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    for grafico in arvore.find_all('canvas'):
        id_grafico = grafico.get('id')
        descrito_por = grafico.get('aria-describedby')
        assert descrito_por, f'{id_grafico}: canvas sem aria-describedby'
        alvo = arvore.find(id=descrito_por)
        assert alvo is not None, \
            f'{id_grafico}: descreve elemento inexistente #{descrito_por}'
        assert len(alvo.get_text(strip=True)) > 40, \
            f'{id_grafico}: descrição de tendência ausente ou vaga'
        assert alvo.find_parent('figure') is grafico.find_parent('figure'), \
            f'{id_grafico}: descrição fora da <figure> do gráfico'


def test_descricao_de_tendencia_traz_os_numeros(dados, client):
    """Conjunto ``dados``: 1 adimplente e 2 inadimplentes; risco baixo 1,
    médio 1, alto 1."""
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    financeiro = arvore.find(id='desc-financeiro').get_text()
    assert '1 adimplente(s)' in financeiro and '2 inadimplente(s)' in financeiro
    risco = arvore.find(id='desc-risco').get_text()
    assert '1 baixo' in risco and '1 médio' in risco and '1 alto' in risco


# ---------------------------------------------------------------------------
# Requisito 9 — mensagens e regiões dinâmicas
# ---------------------------------------------------------------------------
def test_erro_usa_alert_e_nao_fecha_sozinho(dados, client):
    logar_gestora(client)
    client.get('/logout')
    # Login inválido gera flash da categoria "danger".
    resposta = client.post('/login', data={'email': 'gestora@escola.com', 'senha': 'errada'},
                           follow_redirects=True)
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    alerta = arvore.select_one('.alert-danger')
    assert alerta is not None, 'mensagem de erro não renderizada'
    assert alerta.get('role') == 'alert', 'erro deveria usar role="alert"'
    assert 'flash-temporaria' not in (alerta.get('class') or []), \
        'mensagem de erro não deve fechar automaticamente'


def test_confirmacao_usa_status_e_fecha_com_tempo_suficiente(dados, client):
    logar_gestora(client)
    resposta = client.post('/adicionar_instrumento', data={'nome': 'Cavaquinho'},
                           follow_redirects=True)
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    alerta = arvore.select_one('.alert-success')
    assert alerta is not None and alerta.get('role') == 'status'
    assert 'flash-temporaria' in (alerta.get('class') or [])
    assert '}, 10000);' in resposta.get_data(as_text=True), \
        'autofechamento deveria ser de no mínimo 10 segundos'


def test_regiao_atualizada_por_javascript_e_anunciada(app):
    with app.app_context():
        from app import db
        from app.models import Usuario
        from conftest import novo_usuario, novo_aluno, nova_mensalidade
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        for i in range(12):
            nova_mensalidade(novo_aluno(f'Aluno {i:02d}'), 1, False)
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    info = arvore.find(id='paginacaoAtencaoInfo')
    assert info is not None and info.get('aria-live') == 'polite', \
        'contador da paginação sem aria-live'


def test_paginacao_do_financeiro_marca_a_pagina_atual(app):
    with app.app_context():
        from app import db
        from app.models import Usuario
        from conftest import novo_usuario, novo_aluno, nova_mensalidade
        novo_usuario('Gestora', 'gestora@escola.com', Usuario.PAPEL_GESTORA)
        aluno = novo_aluno('Aluno Com Muitas Mensalidades')
        for i in range(60):
            nova_mensalidade(aluno, i, i % 2 == 0)
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    arvore = sopa(client, '/financeiro')
    navegacao = arvore.select_one('nav[aria-label="Páginas de mensalidades"]')
    assert navegacao is not None, 'paginação sem landmark nomeado'
    assert navegacao.select_one('[aria-current="page"]') is not None, \
        'página atual sem aria-current'
    assert not [a for a in navegacao.find_all('a') if a.get('href') == '#'], \
        'item desabilitado ainda é link focável'


# ---------------------------------------------------------------------------
# Requisitos 10 e 11 — foco, movimento e apresentação
#
# Estes testes verificam apenas que as regras existem no CSS entregue. Contraste
# real, ordem de foco e comportamento com zoom continuam dependendo de
# verificação manual (docs/acessibilidade.md).
# ---------------------------------------------------------------------------
def test_css_respeita_preferencia_por_menos_movimento(dados, client):
    logar_gestora(client)
    html = client.get('/dashboard').get_data(as_text=True)
    assert 'prefers-reduced-motion' in html, 'sem tratamento de movimento reduzido'
    assert ':focus-visible' in html, 'sem regra explícita de foco visível'


def test_graficos_desligam_animacao_sob_movimento_reduzido(dados, client):
    """REQ 3/T5: @media CSS não alcança <canvas>; o desligamento da animação
    tem de ser decidido em JavaScript, na criação de cada gráfico Chart.js."""
    logar_gestora(client)
    html = client.get('/dashboard').get_data(as_text=True)
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in html, \
        'dashboard sem consulta a prefers-reduced-motion em JavaScript'
    assert html.count('...semAnimacao') == 6, \
        'cada um dos 6 gráficos Chart.js precisa aplicar animation: false sob reduce'


def test_cores_de_situacao_nao_usam_verde_e_vermelho_puros(dados, client):
    logar_gestora(client)
    html = client.get('/alunos').get_data(as_text=True)
    assert '.adimplente { color: green; }' not in html
    assert '.inadimplente { color: red; }' not in html


def test_situacao_financeira_tem_texto_alem_da_cor(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    marcadores = arvore.select('.inadimplente, .adimplente')
    assert marcadores, 'nenhum marcador de situação encontrado'
    for marcador in marcadores:
        assert marcador.get_text(strip=True) in ('Adimplente', 'Inadimplente'), \
            'a situação financeira depende apenas da cor'


def test_nao_ha_cor_inline_sobrescrevendo_botoes(dados, client):
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    with_estilo = [b for b in arvore.select('a.btn, button.btn')
                   if 'color:' in (b.get('style') or '')]
    assert not with_estilo, \
        f'{len(with_estilo)} controle(s) com cor inline não verificada'


def test_metrica_de_atencao_nao_usa_text_warning(dados, client):
    """#ffc107 sobre branco dá 1,63:1 e não serve para texto."""
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    atencao = arvore.select_one('.texto-atencao')
    assert atencao is not None, 'métrica de atenção sem a classe de contraste corrigido'
    assert atencao.get_text(strip=True).isdigit()
    numeros_em_warning = [e for e in arvore.select('p.text-warning, h1.text-warning, h2.text-warning')]
    assert not numeros_em_warning, 'texto ainda usando .text-warning'


def test_celulas_da_grade_nao_usam_texto_branco(app):
    from datetime import time
    from app import db
    from app.models import Aluno
    from conftest import popular_dados
    ids = popular_dados(app)
    with app.app_context():
        aluno = db.session.get(Aluno, ids['ana'])
        aluno.dia_aula_semana = 1
        aluno.hora_aula = time(14, 0)
        db.session.commit()
    client = app.test_client()
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    for celula in arvore.select('#tabelaOcupacao tbody td'):
        assert 'color:#fff' not in (celula.get('style') or '').replace(' ', ''), \
            'célula da grade com texto branco sobre azul claro'


def test_sem_tabindex_positivo(dados, client):
    """tabindex positivo desloca a ordem de foco em relação à ordem visual."""
    logar_gestora(client)
    for rota, arvore in paginas(client, PAGINAS_GESTORA):
        positivos = [e for e in arvore.select('[tabindex]')
                     if (e.get('tabindex') or '').lstrip('-').isdigit()
                     and int(e['tabindex']) > 0]
        assert not positivos, f'{rota}: {len(positivos)} elemento(s) com tabindex positivo'


def test_regioes_rolaveis_tem_acesso_por_teclado(dados, client):
    """`.table-responsive` transborda horizontalmente em telas estreitas;
    sem foco por teclado o conteúdo excedente fica inalcançável (axe-core:
    scrollable-region-focusable — WCAG 2.1.1)."""
    logar_gestora(client)
    rotas = list(PAGINAS_GESTORA) + [f"/aluno/{dados['ana']}"]
    for rota, arvore in paginas(client, rotas):
        sem_acesso = [d for d in arvore.select('.table-responsive')
                      if d.get('tabindex') != '0']
        assert not sem_acesso, \
            f'{rota}: {len(sem_acesso)} .table-responsive sem tabindex="0"'
    # /login redireciona quem já está autenticado: troca de perfil exige logout.
    client.get('/logout')
    logar_aluno(client)
    arvore = sopa(client, '/minha-area')
    sem_acesso = [d for d in arvore.select('.table-responsive')
                  if d.get('tabindex') != '0']
    assert not sem_acesso, \
        f'/minha-area: {len(sem_acesso)} .table-responsive sem tabindex="0"'


# ---------------------------------------------------------------------------
# Rodada kiro2 — menu do aluno, overlay inerte, aria-live, autocomplete e foco
# ---------------------------------------------------------------------------

def _hrefs_dashboard(arvore):
    """Itens de menu nomeados que apontam para /dashboard.

    Exclui o logo da marca (``.navbar-brand``), que aponta para /dashboard por
    convenção (leva à home) e não é um item redundante: para o aluno a rota
    redireciona a /minha-area. O que não deve existir é o item de menu
    "Dashboard" na lista de navegação.
    """
    itens = arvore.select('.navbar-nav a[href], .list-group a[href]')
    return [a for a in itens if a['href'].rstrip('/') == '/dashboard']


def test_aluno_ve_as_tres_areas_no_menu(dados, client):
    """Requisito 1, revisto em 28/09/2026: a área do aluno foi separada em três
    telas a pedido do professor — Dashboard (pagamentos), Minha Área (dados
    pessoais) e Observações pedagógicas —, então as três aparecem no menu e a
    atual fica marcada. (Antes o Dashboard do aluno repetia a Minha Área e
    ficava fora do menu.)"""
    logar_aluno(client)
    arvore = sopa(client, '/minha-area')
    assert _hrefs_dashboard(arvore), 'o aluno precisa ver o item "Dashboard"'
    for destino in ('/minha-area', '/observacoes'):
        itens = [a for a in arvore.select('nav a[href]') if a['href'].rstrip('/') == destino]
        assert itens, f'o aluno precisa ver o item {destino}'
    minha = [a for a in arvore.select('nav a[href]') if a['href'].rstrip('/') == '/minha-area']
    assert any(a.get('aria-current') == 'page' for a in minha), \
        '"Minha Área" deve estar com aria-current="page" na própria página'


def test_gestora_ve_dashboard_no_menu(dados, client):
    """Requisito 9: o Dashboard é multi-perfil; a gestora vê o item no menu."""
    logar_gestora(client)
    arvore = sopa(client, '/dashboard')
    assert _hrefs_dashboard(arvore), 'a gestora deve ver o item "Dashboard"'


def test_professora_ve_dashboard_no_menu(dados, client):
    """Requisito 9: na base da main o Dashboard serve também a professora
    ("Meu Painel"), então ela mantém o item no menu."""
    logar_professora(client)
    arvore = sopa(client, '/dashboard')
    assert _hrefs_dashboard(arvore), 'a professora deve ver o item "Dashboard"'


def test_aluno_acessa_dashboard_e_ve_o_proprio_painel(dados, client):
    """Requisito 9: o aluno não tem o item "Dashboard" no menu (tem "Minha
    Área"), mas a rota /dashboard o leva ao painel do aluno (comportamento da
    main), não a uma negação."""
    logar_aluno(client)
    resposta = client.get('/dashboard', follow_redirects=True)
    assert resposta.status_code == 200


def test_overlay_inertiza_o_fundo_durante_o_carregamento(dados, client):
    """Requisito 2: a lógica do overlay marca o fundo como inerte enquanto
    carrega e o remove ao ocultar. Verificação estrutural do JS embutido."""
    logar_gestora(client)
    html = client.get('/dashboard').get_data(as_text=True)
    assert "setAttribute('inert'" in html, 'overlay não inertiza o fundo'
    assert "removeAttribute('inert')" in html, 'inertização não é removida'
    # O aviso de carregamento continua sendo anunciado.
    assert 'role="status"' in html


def test_contador_de_financeiro_e_regiao_live(dados, client):
    """Requisito 3.1: o contador de resultados fica em região aria-live."""
    logar_gestora(client)
    arvore = sopa(client, '/financeiro')
    vivos = [d for d in arvore.select('[aria-live="polite"]')
             if 'mensalidade' in d.get_text()]
    assert vivos, 'contador de mensalidades não está em região aria-live'


def test_tabela_de_pagamentos_e_regiao_live(dados, client):
    """Requisito 3.2: a tabela de pagamentos (preenchida por JS) é anunciada."""
    logar_gestora(client)
    arvore = sopa(client, '/financeiro')
    corpo = arvore.find(id='pagamentos-tabela-corpo')
    assert corpo is not None, 'modal de pagamentos sem corpo de tabela'
    live = corpo.find_parent(attrs={'aria-live': 'polite'})
    assert live is not None, 'a tabela de pagamentos não está sob aria-live'


def test_autocomplete_nos_campos_de_login(dados, client):
    """Requisito 4: login tem autocomplete no identificador e na senha."""
    client.get('/logout')
    arvore = sopa(client, '/login')
    email = arvore.find(id='email')
    senha = arvore.find(id='senha')
    assert email.get('autocomplete') in ('email', 'username')
    assert senha.get('autocomplete') == 'current-password'


def test_autocomplete_no_cadastro_de_usuario(dados, client):
    """Requisito 4: nome, e-mail e senha nova no formulário de usuário."""
    logar_gestora(client)
    arvore = sopa(client, '/registrar')
    assert arvore.find(id='nome').get('autocomplete') == 'name'
    assert arvore.find(id='email').get('autocomplete') == 'email'
    assert arvore.find(id='senha').get('autocomplete') == 'new-password'


def test_autocomplete_no_formulario_de_aluno(dados, client):
    """Requisito 4: nome e e-mail do aluno recebem autocomplete."""
    logar_gestora(client)
    arvore = sopa(client, '/alunos')
    nome = arvore.find(id='campo-nome')
    assert nome is not None and nome.get('autocomplete') == 'name'
    # o campo de e-mail do aluno usa o id derivado do nome (campo-email)
    email = arvore.find(id='campo-email')
    assert email is not None and email.get('autocomplete') == 'email'


def test_foco_vai_para_o_resumo_de_erros_ao_reexibir(dados, client):
    """Requisito 5: erro reabre o modal e o resumo recebe foco. Verifica o
    resumo focável e o script que dispara o foco na reabertura."""
    logar_gestora(client)
    resposta = client.post('/adicionar_usuario',
                           data={'nome': 'Sem Senha', 'email': 'novo@escola.com', 'senha': ''})
    assert resposta.status_code == 200
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    # O modal volta marcado para reabrir.
    assert arvore.body.get('data-abrir-modal') == 'novoUsuarioModal'
    # Há um resumo de erros focável.
    resumo = arvore.select_one('.alert[role="alert"][tabindex="-1"]')
    assert resumo is not None, 'sem resumo de erros focável'
    # E o script que move o foco ao abrir o modal está presente.
    html = resposta.get_data(as_text=True)
    assert 'shown.bs.modal' in html and '.focus()' in html


# ---------------------------------------------------------------------------
# Área do aluno e troca de senha — telas nossas, no padrão acima desde 28/09.
# A área do aluno tem três telas (pedido do professor, 28/09): Dashboard
# (pagamentos), Minha Área (dados pessoais) e Observações pedagógicas.
# /alterar-senha também entrou em PAGINAS_ANONIMAS.
# ---------------------------------------------------------------------------
PAGINAS_ALUNO = ['/dashboard', '/minha-area', '/observacoes']


@pytest.mark.parametrize('rota', PAGINAS_ALUNO)
def test_area_do_aluno_segue_o_padrao(dados, client, rota):
    logar_aluno(client)
    arvore = sopa(client, rota)
    verificar_landmarks(arvore, rota)
    verificar_nomes_de_controles(arvore, rota)
    verificar_tabelas(arvore, rota)
    verificar_html_sem_aspas_escapadas(client, rota)

    niveis = [int(t.name[1]) for t in arvore.select('h1, h2, h3, h4, h5, h6')]
    assert niveis[0] == 1, f'{rota}: primeiro título é h{niveis[0]}, não h1'
    for anterior, atual in zip(niveis, niveis[1:]):
        assert atual <= anterior + 1, f'{rota}: salto de h{anterior} para h{atual}'

    expostos = [str(i)[:80] for i in arvore.select('i.fas, i.far, i.fab, i.bi')
                if i.get('aria-hidden') != 'true']
    assert not expostos, f'{rota}: ícone(s) decorativo(s) sem aria-hidden: {expostos}'
    assert not arvore.select('.text-warning'), f'{rota}: texto usando .text-warning'
    sem_acesso = [d for d in arvore.select('.table-responsive') if d.get('tabindex') != '0']
    assert not sem_acesso, f'{rota}: .table-responsive sem tabindex="0"'


def verificar_grafico(resposta, id_grafico, linhas_esperadas):
    """role="img" + aria-label, descrição na mesma <figure> e tabela equivalente
    em <details>; devolve (descrição, linhas da tabela)."""
    html = resposta.get_data(as_text=True)
    arvore = BeautifulSoup(html, 'html.parser')
    grafico = arvore.find(id=id_grafico)
    assert grafico is not None, f'{id_grafico} não renderizado'
    assert grafico.get('role') == 'img' and len(grafico.get('aria-label') or '') > 30
    figura = grafico.find_parent('figure')
    assert figura is not None, f'{id_grafico}: canvas fora de <figure>'
    descricao = arvore.find(id=grafico.get('aria-describedby'))
    assert descricao is not None and descricao.find_parent('figure') is figura
    assert figura.select_one('figcaption details summary').get_text(strip=True)
    tabela = figura.select_one('figcaption details table')
    assert tabela is not None, f'{id_grafico}: sem tabela equivalente em <details>'
    linhas = tabela.select('tbody tr')
    assert len(linhas) == linhas_esperadas, f'{id_grafico}: a tabela não tem os meses do gráfico'
    assert '...semAnimacao' in html, 'o gráfico precisa respeitar prefers-reduced-motion'
    return descricao.get_text(), [[td.get_text(strip=True) for td in tr.find_all('td')] for tr in linhas]


def test_grafico_de_aulas_do_aluno_tem_alternativa_textual(dados, client):
    """Conjunto ``dados``: a Ana tem uma aula há 5 dias, sem marcador de
    presença — nos 6 meses, 0 presenças, 0 faltas e 1 aula sem registro."""
    logar_aluno(client)
    descricao, linhas = verificar_grafico(client.get('/observacoes'), 'aulasMesChart', 6)
    assert 'Total: 1 aula sem presença registrada.' in descricao
    assert [sum(int(v) for v in c) for c in zip(*linhas)] == [0, 0, 1]


def test_grafico_de_mensalidades_do_aluno_tem_alternativa_textual(dados, client):
    """Conjunto ``dados``: a Ana pagou 4 x R$ 250 nos últimos meses e tem 1
    pendente que vence em 20 dias, com a competência do mês corrente."""
    logar_aluno(client)
    descricao, linhas = verificar_grafico(client.get('/dashboard'), 'mensalidadesChart', 12)
    assert 'Total: R$ 1.000,00 pago, R$ 250,00 a vencer.' in descricao
    assert linhas[-1] == ['R$ 0,00', 'R$ 0,00', 'R$ 250,00']       # pago, em atraso, a vencer


def test_erro_da_troca_de_senha_fica_associado_ao_campo(dados, client):
    resposta = client.post('/alterar-senha', data={
        'email': 'professora@escola.com', 'senha_atual': 'errada',
        'nova_senha': 'nova-senha', 'confirmar_senha': 'nova-senha'})
    assert resposta.status_code == 200, 'erro de validação não deve redirecionar'
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')

    resumo = arvore.select_one('.alert[role="alert"][tabindex="-1"]')
    assert resumo is not None, 'sem resumo de erros com role="alert"'
    assert resumo.find('a', href='#campo-senha_atual') is not None

    invalido = arvore.find(id='campo-senha_atual')
    assert invalido.get('aria-invalid') == 'true'
    assert 'campo-senha_atual-erro' in (invalido.get('aria-describedby') or '').split()
    assert arvore.find(id='campo-senha_atual-erro').get_text(strip=True) == \
        'E-mail ou senha atual inválidos.'
    # O e-mail não é marcado: a mensagem não diz qual dos dois está errado.
    assert arvore.find(id='campo-email').get('aria-invalid') is None
    assert arvore.find(id='campo-email').get('value') == 'professora@escola.com'
    for nome in ('senha_atual', 'nova_senha', 'confirmar_senha'):
        assert arvore.find(id=f'campo-{nome}').get('value') == '', \
            f'a senha {nome} não pode voltar preenchida'


def test_troca_de_senha_marca_cada_campo_invalido(dados, client):
    resposta = client.post('/alterar-senha', data={
        'email': 'professora@escola.com', 'senha_atual': 'errada',
        'nova_senha': '123', 'confirmar_senha': '321'})
    arvore = BeautifulSoup(resposta.get_data(as_text=True), 'html.parser')
    for nome in ('senha_atual', 'nova_senha', 'confirmar_senha'):
        assert arvore.find(id=f'campo-{nome}').get('aria-invalid') == 'true', nome
    # A ajuda "Pelo menos 6 caracteres." continua ligada ao campo, junto do erro.
    assert arvore.find(id='campo-nova_senha').get('aria-describedby').split() == \
        ['campo-nova_senha-ajuda', 'campo-nova_senha-erro']
    # O resumo segue a ordem do formulário.
    assert [a['href'] for a in arvore.select('#resumo-erros a')] == \
        ['#campo-senha_atual', '#campo-nova_senha', '#campo-confirmar_senha']

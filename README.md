# SisViolin

Plataforma de gestão orientada a dados para escolas de música, desenvolvida como
**Projeto Integrador em Computação IV** da UNIVESP (DRP01 — Turma 003).

A comunidade externa parceira é o **Studio Duo**, escola de música cuja direção
acolheu o grupo e apontou as necessidades que o sistema procura atender.

## O problema

As informações acadêmicas, administrativas e financeiras da escola estão
distribuídas em meios diferentes. Isso dificulta o acompanhamento da rotina e,
principalmente, o uso desses dados para decidir. Falta uma visão consolidada dos
indicadores que importam no dia a dia.

## O objetivo

Centralizar esses dados numa plataforma responsiva — acessível por computador e
por celular — capaz de gerar indicadores e visualizações que apoiem a gestão.

Indicadores previstos:

- frequência dos alunos
- inadimplência
- receita mensal
- ocupação de horários
- alunos com maior número de faltas
- risco de evasão

## Stack

| Camada | Tecnologia |
|---|---|
| Aplicação | Python 3 + Flask 3.1 |
| ORM | SQLAlchemy 2.0 / Flask-SQLAlchemy |
| Autenticação | Flask-Login (perfis: gestora, professora, aluno) |
| Banco | SQLite no ambiente local · PostgreSQL na nuvem |
| Interface | Jinja2 + Bootstrap 5 + Chart.js |

## Como executar localmente

Requer Python 3.10 ou superior. No Windows (PowerShell), a partir da pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python popular_escola.py    # cria o banco e carrega dados de exemplo
python main.py
```

No Linux ou WSL, troque a linha de ativação por `source .venv/bin/activate`.

A aplicação sobe em <http://127.0.0.1:5000>.

> Para criar o banco vazio, sem dados de exemplo — ou para **atualizar uma base já
> populada** depois de mudanças no modelo (tabelas e índices novos) sem apagar
> nada: `python inicializar_db.py`.

### Banco de dados

Sem a variável `DATABASE_URL` definida, a aplicação usa **SQLite** automaticamente
(`instance/escola_musica.db`). Para apontar a um PostgreSQL, copie `.env.example`
para `.env` e preencha `DATABASE_URL`. O arquivo `.env` não é versionado.

### Usuários de exemplo

Criados por `popular_escola.py`, apenas para desenvolvimento (senha `123456`):

| Perfil | E-mail |
|---|---|
| Proprietária / Gestora | gestora@escola.com |
| Professora | professora@escola.com |
| Aluno | ana@aluno.com |

Estes usuários e os alunos cadastrados são **fictícios**. Nenhum dado real do
Studio Duo deve ser versionado neste repositório.

### Importar a base de análise (MySQL)

A base usada para os indicadores e para a aprendizagem de máquina é um dump
**anonimizado** de um ERP escolar, carregado num MySQL local (schema
`AC_00000000000000`). O `importar_mysql.py` lê esse schema e carrega alunos,
mensalidades, pagamentos e aulas no banco da aplicação, traduzindo curso/série
para os instrumentos existentes. A carga é re-executável e não toca nos dados de
exemplo; nada é escrito no MySQL.

```bash
python importar_mysql.py --dry-run   # só mostra o que faria
python importar_mysql.py             # carga completa (~10 s)
python importar_mysql.py --limpar    # remove o que foi importado
```

A origem vem de `AC_DATABASE_URL` (padrão
`mysql+pymysql://root:root@127.0.0.1:3306/AC_00000000000000`). O dump e o script
de anonimização ficam **fora** do repositório.

### Enviar os dados para o banco da nuvem

`migrar_para_nuvem.py` copia o conteúdo do SQLite local para o Postgres da nuvem
**sem alterar a estrutura de lá** (lê as tabelas que existem e só faz `INSERT`,
preservando os ids; depois avança as sequências). A URL do destino vem de
`NUVEM_DATABASE_URL` (ou `DATABASE_URL`) num arquivo `.env*`, nunca versionado:

```bash
python migrar_para_nuvem.py --dry-run --env .env.migracao   # compara estrutura e conta linhas
python migrar_para_nuvem.py --env .env.migracao             # carrega (destino precisa estar vazio)
python migrar_para_nuvem.py --limpar --env .env.migracao    # apaga as linhas de lá e carrega tudo
```

## Risco de evasão (aprendizagem de máquina)

O score de risco de cada aluno vem de uma **regressão logística** treinada sobre
o histórico da base importada (`app/modelo_evasao.json`). O app pontua em Python
puro (`app/risco.py`), sem scikit-learn em produção; se o JSON não existir, vale
a regra de pesos `RISK_CONFIG` (`app/__init__.py`).

Para re-treinar (lê só o banco da aplicação — nada de MySQL):

```powershell
pip install -r requirements-ml.txt
python -m ml.treinar                  # avalia, exporta app/modelo_evasao.json, escreve instance/ml/metricas.md
python -m ml.treinar --sem-exportar   # só avalia
```

O rótulo é por aluno-ano e sai das próprias mensalidades ("o aluno teve
mensalidade no ano Y; teve em Y+1?"; no ano corrente, o cadastro inativo), as
features usam só o histórico até 31/12 de cada ano (sem ver o futuro) e a
validação é cruzada agrupada por aluno e temporal (treina até 2024, testa 2025).
As métricas, o peso de cada feature e as limitações da base ficam em
`instance/ml/metricas.md` a cada treino.

## Frequência

A presença fica registrada na observação da aula como `Presença: nP/mA` — o
importador grava assim a frequência do ERP e o formulário de acompanhamento grava
o mesmo marcador quando a professora escolhe *Presente* ou *Faltou*. Sobre isso o
dashboard mostra presença por instrumento, alunos com mais faltas e aulas por dia
da semana (`app/indicadores.py`, uma consulta), e a ficha do aluno mostra a taxa.

## Testes

```powershell
pip install -r requirements-dev.txt
pytest                  # tudo (~15 s)
pytest -m "not slow"    # sem os testes de desempenho
```

Cada teste sobe a aplicação com um SQLite **em memória** — nada toca em
`instance/`. A pasta `tests/` cobre segurança (cadastro fechado, CSRF), regras
financeiras e de risco dos modelos, as rotas por perfil, a transformação do
importador (sem MySQL), o modelo de evasão (features com corte temporal,
pontuação idêntica ao scikit-learn, motivos, fallback para a regra) e o
desempenho com ~10 mil mensalidades (cada página gerencial em menos de 1 s e
sem consultas N+1, com o modelo ligado).

## Estrutura

```
sisviolin/
├── main.py               # ponto de entrada local
├── api/index.py          # ponto de entrada para deploy serverless
├── inicializar_db.py     # cria/atualiza tabelas, índices e instrumentos sem apagar dados
├── popular_escola.py     # carrega dados de exemplo (apaga tudo antes)
├── importar_mysql.py     # importa a base anonimizada do ERP (MySQL) para a aplicação
├── migrar_para_nuvem.py  # envia os dados do SQLite local para o Postgres da nuvem
├── requirements.txt
├── requirements-dev.txt  # + pytest
├── requirements-ml.txt   # + pandas, scikit-learn (só para treinar)
├── pytest.ini
├── vercel.json
├── app/
│   ├── __init__.py       # create_app(), resolução do banco, RISK_CONFIG
│   ├── models.py         # Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula
│   ├── risco.py          # features, pontuação pelo modelo (JSON) e motivos
│   ├── indicadores.py    # presença por instrumento, faltosos, aulas por dia da semana
│   ├── modelo_evasao.json# regressão logística exportada por ml/treinar.py
│   ├── routes.py         # rotas, autenticação e controle por perfil
│   ├── forms.py          # formulários (Flask-WTF)
│   └── templates/        # Jinja2 + Bootstrap
├── ml/
│   ├── rotulos.py        # rótulo de evasão por aluno-ano, derivado das mensalidades
│   └── treinar.py        # monta a base, avalia (CV + temporal) e exporta o JSON
└── tests/                # pytest, SQLite em memória
```

## Situação atual

Protótipo funcional em evolução. O indicador de risco de evasão passou a ser um
modelo treinado (ver acima); a regra de pesos `RISK_CONFIG` ficou como fallback.
A base de treino é um proxy anonimizado de ERP escolar — o modelo deve ser
re-treinado com os dados reais da escola quando existirem.

## Créditos e origem do código

Esta base foi adaptada do projeto [`escola_de_musica_v1`](https://github.com/ptrsantos/escola_de_musica_v1),
de Paulo de Tarso Rodrigues dos Santos, integrante do grupo, que por sua vez
partiu do sistema de fluxo de caixa desenvolvido em Projeto Integrador anterior.

## Equipe

Flávio Máximo · Lucas Máximo · Paulo de Tarso Rodrigues dos Santos ·
Ronaldo Emidio · Sandra Camargo · Stephany Dias Tavares · Thales S. Ferreira ·
Tiago Augusto Libanoro de Oliveira

Orientador: Davi Alex Nogueira

## Licença

[MIT](LICENSE).

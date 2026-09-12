# Escola de Música — sistema adaptado do Fluxo de Caixa

Aplicação web (Python + Flask) de gestão e análise para uma escola de música.
Foi **adaptada a partir do projeto original `fluxo_de_caixa_p3`**, mantendo a
mesma identidade visual e a mesma estrutura funcional (navbar azul, barra
lateral, cards de métrica, gráficos Chart.js, spinner de carregamento e
modais), agora voltada ao domínio escolar.

## Como executar

Depois de clonar o repositório, siga os passos na ordem:

```powershell
# 1. Criar o ambiente virtual
python -m venv .venv

# 2. Ativar o ambiente virtual
.\.venv\Scripts\Activate.ps1         # Linux/Mac: source .venv/bin/activate

# 3. Instalar as dependências
pip install -r requirements.txt

# 4. Migrar o banco e inserir os dados iniciais (um único comando)
python popular_escola.py

# 5. Rodar a aplicação
python main.py                       # http://127.0.0.1:5000
```

> **Banco de dados:** sem a variável `DATABASE_URL` definida, a aplicação usa
> SQLite local (arquivo em `instance/`). Para usar Postgres (nuvem), defina
> `DATABASE_URL` no arquivo `.env` (ver `.env.example`). O passo 4 funciona nos
> dois casos: ele cria as tabelas, aplica a migração das colunas de horário de
> aula e popula os dados de exemplo, sendo seguro rodar mais de uma vez.

> **São apenas dois scripts Python na raiz:** `popular_escola.py` (migração +
> dados iniciais) e `main.py` (executa a aplicação).

## Usuários de teste (senha `123456`)

| Perfil       | E-mail                  |
|--------------|-------------------------|
| Proprietária | gestora@escola.com      |
| Professora   | professora@escola.com   |
| Aluno        | ana@aluno.com           |

## Mapeamento da adaptação (Fluxo de Caixa → Escola de Música)

| Fluxo de Caixa (original) | Escola de Música (adaptado)             |
|---------------------------|-----------------------------------------|
| `Usuario`                 | `Usuario` + campo `papel` (perfis)      |
| `Categoria`               | `Instrumento`                           |
| `Transacao`               | `Mensalidade` + `Pagamento`             |
| (novo)                    | `Aluno`, `Aula` (acompanhamento)        |
| Dashboard (bar/line/pie)  | Dashboard (situação financeira, recebido por mês, alunos por instrumento) + gráfico de risco |
| Transações / Categorias   | Alunos / Mensalidades / Acompanhamento  |
| Relatórios                | Relatórios (visão consolidada + risco)  |

## Requisitos da escola atendidos

- **Perfis**: proprietária/gestora, professora e aluno, com navegação e
  permissões próprias (controle por perfil nas rotas).
- **Cadastro de alunos**: nome, nascimento, endereço, e-mail, instrumento e
  mensalidade.
- **Financeiro**: mensalidades por competência, pagamentos e status
  automático (`pago`/`pendente`/`em atraso`) → inadimplência.
- **Acompanhamento pedagógico**: aulas com observações e orientações de estudo.
- **Análise de risco de evasão**: combina inadimplência (peso maior) e
  ausência de acompanhamento recente, classificando em baixo/médio/alto e
  mostrando os fatores. Pesos em `RISK_CONFIG` (`app/__init__.py`).
- **Painel gerencial**: indicadores e gráficos, com lista de alunos que pedem
  atenção.
- **Área do aluno**: consulta apenas dos próprios dados (bloqueio HTTP 403
  para dados de outros alunos).

## Estrutura

```
escola_de_musica_v1/
├── main.py                 # ponto de entrada: executa a aplicação
├── popular_escola.py       # migra o schema + insere os dados iniciais
├── requirements.txt
├── vercel.json             # configuração de deploy (Vercel)
├── api/
│   └── index.py            # handler WSGI para a Vercel
└── app/
    ├── __init__.py         # factory create_app/init_db + RISK_CONFIG
    ├── models.py           # Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula
    ├── routes.py           # rotas, autenticação e controle por perfil
    ├── forms.py            # formulários (Flask-WTF)
    └── templates/          # telas (dashboard, início, alunos, financeiro, etc.)
```

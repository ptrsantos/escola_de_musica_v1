# Escola de Música — sistema adaptado do Fluxo de Caixa

Aplicação web (Python + Flask) de gestão e análise para uma escola de música.
Foi **adaptada a partir do projeto original `fluxo_de_caixa_p3`**, mantendo a
mesma identidade visual e a mesma estrutura funcional (navbar azul, barra
lateral, cards de métrica, gráficos Chart.js, spinner de carregamento e
modais), agora voltada ao domínio escolar.

## Como executar

```powershell
cd escola_de_musica_adaptada
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

python popular_escola.py             # cria e popula o banco com dados de exemplo
python main.py                       # http://127.0.0.1:5000
```

> Só inicializar o banco (sem dados de exemplo): `python inicializar_db.py`.

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
escola_de_musica_adaptada/
├── main.py                 # ponto de entrada (igual ao original)
├── inicializar_db.py       # cria tabelas + instrumentos
├── popular_escola.py       # popula dados de exemplo
├── requirements.txt
└── app/
    ├── __init__.py         # factory create_app/init_db + RISK_CONFIG
    ├── models.py           # Usuario, Instrumento, Aluno, Mensalidade, Pagamento, Aula
    ├── routes.py           # rotas, autenticação e controle por perfil
    ├── forms.py            # formulários (Flask-WTF)
    └── templates/          # identidade visual do fluxo de caixa adaptada
```

# Plano de Implementação — Nova Página Inicial (Home Operacional)

> **Requisito de origem:** `requisito_09092026.txt`
> **Data do plano:** 11/09/2026
> **Status:** IMPLEMENTADO e verificado em 11/09/2026. Ver seção 11 (Registro de execução) para os desvios em relação ao plano original.

---

## 1. Objetivo do requisito (o que a PO pediu)

Transformar a **tela inicial pós-login** em um painel operacional de acesso rápido, pensado para o momento de dar aula. Em vez de uma visão gerencial (a dashboard atual, com gráficos e risco de evasão), a PO quer, logo ao abrir o sistema, ver o que precisa **hoje**:

1. **Agenda do dia** — lista dos alunos que têm aula no dia atual (exemplo dado: "alunos que terão aula de quarta-feira"), com atalho direto para a ficha do aluno para registrar as anotações da aula.
2. **Avisos** — destaque para aniversariantes (aniversário de aluno).
3. **Balanço de entrada** — resumo rápido do que foi recebido (entradas financeiras).

Frase-guia da PO: *"se eu for dar aula agora 15h, já abro o sistema e na página inicial já consigo acessar o perfil do aluno, e posso fazer as anotações."*

O norte de UX é **mínimo de cliques** entre abrir o sistema e chegar na ficha do aluno para anotar.

---

## 2. Análise de lacunas (o que falta no sistema atual)

### 2.1 Lacuna crítica: não existe agendamento recorrente de aulas

- Hoje o modelo `Aula` (`app/models.py`) registra apenas aulas **que já aconteceram** — campos `data`, `observacao`, `orientacao_estudo`. É um histórico do passado.
- O requisito "alunos que terão aula de quarta-feira" pressupõe um **horário fixo semanal** por aluno (ex.: Ana tem aula toda quarta às 15h).
- **Não há como derivar a "agenda do dia" dos dados atuais.** É preciso registrar o dia da semana (e idealmente o horário) de aula recorrente de cada aluno.

**Decisão de modelagem adotada neste plano:** adicionar os campos de recorrência **diretamente no `Aluno`** (`dia_aula_semana` e `hora_aula`), assumindo o cenário mais comum de escola de música: cada aluno tem **um** horário fixo por semana. Essa é a solução mais simples e atende ao requisito literal.

> **Alternativa (não adotada agora):** criar uma entidade `HorarioAula` (aluno_id, dia_semana, hora) para suportar múltiplos horários por aluno na semana. Fica registrada como evolução futura caso a escola precise de mais de uma aula semanal por aluno. Ver seção 9.

### 2.2 Lacuna: aniversário depende de `data_nascimento`

- O campo `Aluno.data_nascimento` já existe e é opcional. Serve para calcular aniversariantes.
- Alunos sem data de nascimento cadastrada simplesmente não aparecem nos avisos (comportamento aceitável).

### 2.3 Lacuna: a home atual redireciona tudo para a dashboard gerencial

- Rota `/` (`home`) hoje: se logado, redireciona para `dashboard`.
- `dashboard` é analítica (gráficos Chart.js, risco, consolidado). Não é o "meu dia".
- Precisamos de uma **nova página inicial operacional** que passe a ser o destino pós-login (mantendo a dashboard gerencial acessível pelo menu).

### 2.4 Decisão sobre biblioteca de calendário

- **Não será usada biblioteca de calendário pesada** (ex.: FullCalendar) nesta primeira entrega. O requisito é uma **lista do dia**, não um calendário mensal navegável.
- A "agenda do dia" será uma **lista/tabela** dos alunos com aula hoje, ordenada por horário — implementada com HTML + Bootstrap (já em uso no projeto).
- O cálculo de "dia da semana de hoje" e de "aniversariantes" usa apenas a biblioteca padrão `datetime` do Python. Sem dependências novas.
- **Evolução futura opcional:** integrar FullCalendar (JS via CDN) para uma visão semanal/mensal navegável. Registrado na seção 9, fora do escopo atual.

---

## 3. Escopo desta entrega

**Dentro do escopo:**
- Novo painel inicial (home operacional) com os 3 blocos: Agenda do dia, Avisos (aniversariantes) e Balanço de entrada.
- Campos de horário de aula recorrente no cadastro de aluno.
- Ajuste de navegação (o que é destino pós-login e o que fica no menu).
- Migração/atualização do banco para os novos campos.

**Fora do escopo (evoluções futuras — seção 9):**
- Múltiplos horários por aluno na semana.
- Calendário mensal navegável (FullCalendar).
- Notificações/e-mails de aniversário.
- Reagendamento e controle de presença por aula.

---

## 4. Alterações no banco de dados

### 4.1 Novos campos no modelo `Aluno`

| Campo | Tipo | Nulo? | Descrição |
|---|---|---|---|
| `dia_aula_semana` | `Integer` | sim | Dia fixo da aula na semana. Convenção **Python `weekday()`**: 0 = segunda … 6 = domingo. `NULL` = aluno sem horário fixo definido. |
| `hora_aula` | `Time` | sim | Horário da aula (ex.: 15:00). `NULL` = não informado. |

> Convenção de dia da semana: adotar `weekday()` do Python (segunda=0) para casar diretamente com `date.today().weekday()` sem conversões. Documentar isso no código.

### 4.2 Estratégia de atualização do schema

> **AJUSTE NA EXECUÇÃO:** O ambiente local **não usa SQLite** como o plano assumia — o `.env` aponta para um **Postgres na nuvem (Neon)**. Como as tabelas já existiam, `db.create_all()` não adicionou as novas colunas. A migração (ALTER TABLE idempotente, compatível com Postgres e SQLite, que adiciona `dia_aula_semana` e `hora_aula` só se faltarem) foi **consolidada dentro do próprio `popular_escola.py`** (função `migrar_schema()`), de modo que um único comando — `python popular_escola.py` — garante o schema e popula os dados. Não há script de migração separado.

- **Ambiente local (SQLite):** os campos são **nulos**, então a forma mais simples e segura é:
  - Opção A (recomendada p/ este projeto): recriar/popular o banco de exemplo via `popular_escola.py` (que já apaga e recria dados), incluindo os novos campos. Como o projeto usa banco de exemplo, isso é aceitável em dev.
  - Opção B (preservando dados): usar Flask-Migrate (já instalado) — `flask db migrate` + `flask db upgrade`. Requer configurar o entrypoint de migração. Recomendado se já houver dados reais.
- **Ambiente de produção (Postgres na Vercel):** usar **Flask-Migrate** para aplicar a migração sem perda de dados. Como as colunas são nulas, a migração é não-destrutiva.

> **Ação de decisão para a PO/equipe:** confirmar se há dados reais em produção. Se sim, seguir com Flask-Migrate (Opção B). Se for só ambiente de exemplo, Opção A basta.

### 4.3 Impacto em cascata

- `popular_escola.py` deve passar a definir `dia_aula_semana` e `hora_aula` para os alunos de exemplo, garantindo que a agenda do dia tenha dados demonstráveis (incluindo pelo menos um aluno com aula "hoje").

---

## 5. Alterações nas classes (backend)

### 5.1 `app/models.py`

**5.1.1 Modelo `Aluno` — novos campos**
- Adicionar `dia_aula_semana` (Integer, nullable) e `hora_aula` (Time, nullable).

**5.1.2 `Aluno` — novas propriedades utilitárias**
- `nome_dia_aula` → retorna o nome do dia por extenso em português ("Segunda-feira"…"Domingo") ou `None`. Facilita exibir na UI.
- `tem_aula_hoje` → `True` se `dia_aula_semana == date.today().weekday()`.
- `proximo_aniversario` / `dias_para_aniversario` → auxiliares para ordenar aniversariantes.
- `faz_aniversario_hoje` → compara dia/mês de `data_nascimento` com hoje.

**5.1.3 Novas funções de consulta (helpers de domínio)**
Criar funções (em `models.py` ou num novo `app/services.py` — ver 5.4) para encapsular a lógica da home:
- `alunos_com_aula_no_dia(dia_semana)` → lista de `Aluno` ativos cujo `dia_aula_semana` == dia informado, ordenados por `hora_aula`.
- `aniversariantes_do_dia(dia)` e `aniversariantes_do_mes(mes)` → alunos ativos que fazem aniversário hoje / no mês corrente.
- `balanco_entradas(inicio, fim)` → soma de `Pagamento.valor` no período (dia atual e mês atual). Usa `func.sum` do SQLAlchemy.

**5.1.4 Mapa de nomes de dias**
- Constante `DIAS_SEMANA_PT = ['Segunda-feira', 'Terça-feira', ..., 'Domingo']` para exibição e para popular o `SelectField` do formulário.

### 5.2 `app/forms.py`

**`AlunoForm` — novos campos:**
- `dia_aula_semana` → `SelectField` (coerce int, `Optional`), com choices vindas de `DIAS_SEMANA_PT` mais uma opção vazia ("Sem horário fixo").
- `hora_aula` → campo de hora. Usar `wtforms.TimeField` (WTForms 3.x suporta) com `format='%H:%M'`, validador `Optional`.

> Observação: hoje as rotas leem `request.form` diretamente e **não** usam os WTForms (validações declaradas não são aplicadas). Este plano mantém o padrão atual para não misturar refatorações; os novos campos serão lidos via `request.form` nas rotas de aluno. Os campos no form ficam documentados/consistentes, mas a leitura efetiva segue o padrão vigente. (Melhoria de passar a usar `validate_on_submit()` fica registrada na seção 9.)

### 5.3 `app/routes.py`

**5.3.1 Nova rota / repropósito da home**
- Alterar `home` (`/`) e o fluxo pós-login:
  - Criar **`inicio`** (`/inicio`) — a nova página inicial operacional, `@login_required`.
  - Para **gestora e professora**: após login, `dashboard` passa a redirecionar (ou o `home`) para `inicio`. A dashboard gerencial continua acessível em `/dashboard` pelo menu.
  - Para **aluno**: mantém o redirecionamento para `minha_area` (o painel operacional é voltado a quem dá aula).
  - Decisão de rota: manter `/dashboard` como está (gerencial) e introduzir `/inicio` como novo destino padrão de gestora/professora. Ajustar `home()` para redirecionar autenticados a `inicio` (aluno para `minha_area`).

**5.3.2 Lógica da rota `inicio`**
Montar o contexto com:
- `agenda_hoje` = `alunos_com_aula_no_dia(date.today().weekday())` — com nome, horário e link para `aluno_detalhe`.
- `dia_semana_atual` = nome do dia por extenso.
- `aniversariantes_hoje` e `aniversariantes_mes`.
- `entradas_hoje` = `balanco_entradas` do dia; `entradas_mes` = do mês corrente.
- (Opcional) contadores rápidos: nº de aulas hoje, nº de aniversariantes.

**5.3.3 Rotas de aluno (`adicionar_aluno`, `editar_aluno`)**
- Passar a ler e persistir `dia_aula_semana` (int ou None) e `hora_aula` (parse `HH:MM` → `time`, ou None).
- Adicionar helper `parse_time(value)` análogo ao `parse_date` já existente.

### 5.4 (Opcional) `app/services.py`

- Para manter `routes.py` enxuto, encapsular as consultas da home (agenda, aniversariantes, balanço) num módulo de serviço. **Decisão:** opcional; se preferirmos menor footprint, as funções ficam em `models.py`. Recomendo `services.py` pela clareza, mas não é bloqueante.

---

## 6. Alterações na interface web (templates)

### 6.1 Novo template `app/templates/inicio.html`
Estende `base.html`. Estrutura em três blocos usando o grid do Bootstrap (já disponível):

**Bloco 1 — Agenda do dia (destaque principal, topo)**
- Título dinâmico: "Agenda de hoje — {{ dia_semana_atual }}, {{ data }}".
- Tabela/lista de cards: nome do aluno, instrumento, horário (`hora_aula`), e botão **"Abrir ficha / anotar"** que leva a `aluno_detalhe(id=aluno.id)`.
- Ordenada por horário.
- Estado vazio: "Nenhuma aula agendada para hoje."
- Este bloco atende diretamente o "acessar o perfil do aluno e fazer anotações".

**Bloco 2 — Avisos / Aniversariantes (lateral ou abaixo)**
- Lista de aniversariantes do dia com destaque (ícone de bolo/`fa-birthday-cake`).
- Sub-lista de aniversariantes do mês (menos destaque).
- Estado vazio: "Nenhum aniversário hoje."

**Bloco 3 — Balanço de entrada (cards de métrica)**
- Card "Entradas de hoje" (R$) e "Entradas do mês" (R$), no mesmo estilo dos cards de métrica já usados na dashboard.
- Link "Ver financeiro" → rota `financeiro` (apenas gestora).

### 6.2 `app/templates/base.html` — navegação
- Adicionar item de menu **"Início"** (ícone `fa-home`) apontando para `inicio`, visível para gestora e professora, marcado como ativo quando `request.endpoint == 'inicio'`.
- Manter o item **"Dashboard"** apontando para `/dashboard` (visão gerencial), agora como item separado do "Início".
- Ajustar o realce (`active`) para não conflitar entre `inicio` e `dashboard`.

### 6.3 `app/templates/alunos.html` e `aluno_detalhe.html` — cadastro/edição
- Adicionar ao formulário de cadastro/edição de aluno os campos:
  - **Dia da aula** (select: Segunda…Domingo + "Sem horário fixo").
  - **Horário da aula** (`<input type="time">`).
- Exibir na ficha (`aluno_detalhe.html`) o horário de aula recorrente ("Aula: Quarta-feira às 15:00").

### 6.4 Consistência visual
- Reusar classes de card, cores da navbar e o padrão de ícones Font Awesome já presentes, mantendo a identidade do projeto.

---

## 7. Scripts auxiliares

### 7.1 `popular_escola.py`
- Definir `dia_aula_semana` e `hora_aula` para os 5 alunos de exemplo.
- Garantir que **pelo menos um aluno tenha aula no dia da semana de hoje** para a agenda não aparecer vazia na demonstração (calcular `date.today().weekday()` no seed e atribuir a um ou dois alunos).
- Opcional: ajustar uma `data_nascimento` para cair "hoje" e demonstrar o bloco de aniversariantes (ou documentar que aniversário aparece conforme a data real).

### 7.2 Consolidação de scripts
- O projeto passou a ter **apenas dois scripts Python na raiz**: `popular_escola.py` (migração de schema + dados iniciais) e `main.py` (executa a aplicação). Os antigos `inicializar_db.py` e `_diag_db.py` foram removidos por redundância — a criação de tabelas/instrumentos ocorre via `init_db` dentro do `popular_escola.py`.

---

## 8. Sequência de execução (passo a passo)

Ordem recomendada de implementação, do backend para o frontend, verificando a cada etapa:

1. **Modelo de dados** — adicionar `dia_aula_semana` e `hora_aula` ao `Aluno`; criar constante `DIAS_SEMANA_PT` e as propriedades utilitárias (`nome_dia_aula`, `tem_aula_hoje`, `faz_aniversario_hoje`).
2. **Funções de consulta** — implementar `alunos_com_aula_no_dia`, `aniversariantes_do_dia`, `aniversariantes_do_mes`, `balanco_entradas` (em `services.py` ou `models.py`).
3. **Migração do banco** — aplicar via Flask-Migrate (produção/dados reais) ou recriar via `popular_escola.py` (dev). Confirmar estratégia com a equipe (seção 4.2).
4. **Formulário** — adicionar campos ao `AlunoForm` e helper `parse_time` em `routes.py`.
5. **Rotas de aluno** — atualizar `adicionar_aluno` e `editar_aluno` para persistir os novos campos.
6. **Rota da home** — criar `inicio` (`/inicio`) e ajustar `home()`/fluxo pós-login (gestora/professora → `inicio`; aluno → `minha_area`).
7. **Template `inicio.html`** — construir os três blocos (Agenda, Avisos, Balanço).
8. **Template `base.html`** — adicionar o item de menu "Início" e ajustar realce.
9. **Templates de aluno** — adicionar campos de dia/horário no cadastro/edição e exibir na ficha.
10. **Seed** — atualizar `popular_escola.py` com horários e um aluno com aula "hoje".
11. **Verificação** — rodar `popular_escola.py`, subir `main.py`, logar como gestora e professora, validar: agenda do dia correta, aniversariantes, balanço de entradas, e o clique do aluno abrindo a ficha para anotar. Validar login de aluno (não deve ver a home operacional, vai para Minha Área).
12. **Regressão** — conferir que dashboard, financeiro, alunos, acompanhamento e relatórios continuam funcionando; conferir tratamento de aluno sem `dia_aula_semana`/`data_nascimento` (não quebrar).

---

## 9. Evoluções futuras (fora do escopo atual)

- **Múltiplos horários por aluno**: entidade `HorarioAula` (aluno_id, dia_semana, hora) para alunos com mais de uma aula semanal.
- **Calendário navegável**: integrar FullCalendar (CDN) para visão semanal/mensal.
- **Controle de presença**: marcar comparecimento por aula agendada.
- **Notificações de aniversário**: e-mail/aviso automático.
- **Uso efetivo dos WTForms** nas rotas (`validate_on_submit`) para validação de entrada mais robusta.
- **Agenda considerando feriados/exceções** e reagendamentos pontuais.

---

## 10. Riscos e pontos de decisão

| Item | Decisão necessária |
|---|---|
| Um ou vários horários por aluno na semana | Este plano assume **um**. Se a escola precisar de vários, subir para a entidade `HorarioAula` antes de implementar. |
| Dados reais em produção | Define se usamos Flask-Migrate (preserva dados) ou recriação via seed (dev). |
| Home padrão pós-login | Confirmado: gestora/professora → `inicio`; aluno → `minha_area`. Dashboard gerencial permanece no menu. |
| Biblioteca de calendário | Não usar nesta entrega; lista simples do dia com Bootstrap. FullCalendar fica como evolução. |

---

## 11. Registro de execução (o que foi feito e desvios do plano)

Implementação concluída e verificada em 11/09/2026. Resumo do que foi entregue e onde a execução divergiu do plano original.

### 11.1 Arquivos alterados/criados
- `app/models.py` — campos `dia_aula_semana`/`hora_aula` no `Aluno`; constante `DIAS_SEMANA_PT`; propriedades `nome_dia_aula`, `hora_aula_formatada`, `horario_aula_descricao`, `tem_aula_hoje`, `faz_aniversario_hoje`, `faz_aniversario_no_mes`; funções `alunos_com_aula_no_dia`, `aniversariantes_do_dia`, `aniversariantes_do_mes`, `balanco_entradas`.
- `app/forms.py` — `AlunoForm` com `dia_aula_semana` (SelectField) e `hora_aula` (TimeField).
- `app/routes.py` — helpers `parse_time` e `parse_dia_semana`; nova rota `/inicio`; ajuste de `home()` e da rota `login` (redirecionamento por papel); persistência dos novos campos em `adicionar_aluno`/`editar_aluno`.
- `app/__init__.py` — context processor passa a expor `DIAS_SEMANA_PT` aos templates.
- `app/templates/inicio.html` — **novo**, com os três blocos (balanço de entradas, agenda do dia, avisos/aniversariantes).
- `app/templates/base.html` — item de menu "Início" (navbar e sidebar) para gestora/professora.
- `app/templates/alunos.html` e `aluno_detalhe.html` — campos de dia/horário no cadastro/edição e exibição do horário na ficha.
- `popular_escola.py` — seed com horários; 2 alunos com aula "hoje" e 1 aniversariante "hoje". **Também contém a migração de schema embutida** (`migrar_schema()`), tornando-o autossuficiente: um único comando migra e popula.

### 11.2 Desvios em relação ao plano
1. **Banco de produção também é o banco local (Postgres/Neon), não SQLite.** Consequência: em vez de recriar o banco (Opção A da seção 4.2), foi criado `migrar_horario_aula.py` para aplicar `ALTER TABLE` sem perder dados. Esse script atende tanto o ambiente atual quanto qualquer SQLite futuro.
2. **Ajuste adicional na rota `login`.** O plano previa ajustar apenas `home()`. Na prática, o `login` redirecionava fixo para `/dashboard`; foi alterado para respeitar `?next=` e então enviar gestora/professora → `/inicio` e aluno → `/minha-area`.
3. **`DIAS_SEMANA_PT` exposto via context processor** (em vez de passar por cada rota), simplificando o uso nos templates de cadastro/edição de aluno.
4. **Flask-Migrate não foi inicializado.** Como não havia pasta `migrations/` e a mudança era de duas colunas nulas, optou-se pelo script ALTER TABLE direto (mais simples e sem introduzir a estrutura de migrations agora). Fica como evolução caso o projeto passe a versionar schema formalmente.

### 11.3 Verificações realizadas
- `migrar_horario_aula.py` criou as colunas no Postgres com sucesso (idempotente).
- `popular_escola.py` populou sem erros.
- Funções de consulta retornaram: agenda de hoje (Ana 15:00, Carla 16:30), aniversariante de hoje (Carla), entradas do mês (R$ 830,00).
- Test client: `/inicio` responde 200 com agenda, aniversariantes e entradas; `/` e `login` redirecionam por papel (gestora/professora → `/inicio`, aluno → `/minha-area`); aluno é bloqueado em `/inicio`.
- Edição de aluno persiste `dia_aula_semana`/`hora_aula` e a opção "Sem horário fixo" grava `None`.
- Regressão OK: `/dashboard`, `/alunos`, `/financeiro`, `/acompanhamento`, `/relatorios` continuam respondendo 200.

### 11.5 Calendário de aulas (FullCalendar) — adicionado e depois REMOVIDO
- Foi incluído em caráter experimental um FullCalendar no rodapé da página `inicio`, mas **removido a pedido** (visual insatisfatório e problemas de carregamento dos dados). A página voltou aos três blocos originais (agenda do dia, aniversariantes, balanço de entradas). Um calendário navegável permanece como possível evolução futura (seção 9), se retomado com abordagem diferente.

### 11.4 Observação de deploy
- Em produção (Vercel), rodar `python popular_escola.py` uma vez contra o banco de produção aplica a migração de schema embutida e recria os dados. **Atenção:** esse script apaga os dados transacionais existentes (usuários, alunos, mensalidades, etc.) antes de repopular — não usar em produção com dados reais. Para produção com dados reais, extrair apenas a função `migrar_schema()` (ou adotar Flask-Migrate no pipeline) para aplicar as colunas sem apagar dados.

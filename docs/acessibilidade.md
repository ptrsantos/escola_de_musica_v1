# Acessibilidade — registro de verificação

Alvo: **WCAG 2.2 nível AA**.
Escopo: os templates de `app/templates`, o CSS e o JavaScript embutidos.

Três rodadas de trabalho registradas neste documento:

| | Rodada 1 | Rodada 2 | Rodada 3 |
|---|---|---|---|
| Data | 23/09/2026 | 26/09/2026 | 26/09/2026 |
| Executado por | Kiro (agente), tarefas 1 a 14 da spec `.kiro/specs/acessibilidade-frontend` | Qoder (agente), tarefas T1–T13 da spec `.qode/specs/melhorias-acessibilidade-qode` | Kiro (agente), tarefas 1 a 7 da spec `.kiro/specs/melhorias-acessibilidade-kiro2` |
| Verificação automatizada | `pytest tests/test_acessibilidade.py` — 98 testes | 119 testes estruturais + auditoria dinâmica axe-core 4.10.2 | 130 testes estruturais |
| Ferramentas | pytest 9.1.1, beautifulsoup4 4.12.3, cálculo de contraste WCAG em script próprio | pytest 9.1.1, beautifulsoup4 4.12.3, axe-core 4.10.2 via navegador | pytest 9.1.1, beautifulsoup4 4.12.3 |
| Situação | Correções de código concluídas | Melhorias concluídas · **validação manual pendente** | Melhorias concluídas · **validação manual pendente** |

As rodadas 1 e 2 seguiram as regras da skill `acessibilidade-web`
(`.kiro/skills/acessibilidade-web`). A rodada 3 acrescentou a skill
`.kiro/skills/acessibilidade-navegacao-foco`, que documenta navegação por
perfil, gerenciamento de foco e regiões dinâmicas.

> **Este documento não declara conformidade.** Ele separa o que foi verificado do
> que continua sem verificação. Conformidade AA exige teste com tecnologia
> assistiva real e revisão por especialista — nada disso foi feito ainda.

## 1. O que foi corrigido

| Área | Antes | Agora |
|---|---|---|
| Landmarks | `<div class="main-content">`, sidebar em `<div>` | `<main id="conteudo-principal">`, duas `<nav>` com rótulos distintos |
| Atalho de teclado | inexistente | "Pular para o conteúdo" como primeiro elemento focável |
| Títulos | páginas começavam em `<h2>`; números eram `<h2>` | um `<h1>` por página; métricas em `<p class="h2">` |
| Rótulos | 2 de 9 formulários com `for`/`id` | todos os campos via macros de `_a11y.html` |
| Campos duplicados | `dia_aula_semana` e `hora_aula` repetidos no mesmo formulário | um par por formulário |
| Erros | flash genérico + redirect, valores perdidos | erro por campo com `aria-invalid`/`aria-describedby`, resumo com foco, valores preservados |
| Botões de ícone | só `title` | `aria-label` citando o registro; 31 ícones decorativos com `aria-hidden` |
| Modais | sem nome acessível | `aria-labelledby` nos 14; `aria-describedby` nos de exclusão; fechar nomeado |
| Tabelas | sem `caption`/`scope` | `caption` e `scope` em todas, inclusive as novas |
| Gráficos | 6 `<canvas>` sem alternativa | `role="img"` + `aria-label` + tabela equivalente em `<details>` |
| Mensagens | tudo fechava em 3 s | erro permanece; confirmação fecha em 10 s; `role` por categoria |
| Paginação | `href="#"`, sem página atual | `aria-current`, item inativo como `<span>`, `aria-live` no contador |
| Movimento | sem tratamento | `prefers-reduced-motion` em `base.html` e `dashboard.html` |
| Foco | apenas sombra em campos | `:focus-visible` com contorno de 6,44:1 |

### Rodada 2 (26/09/2026) — melhorias sobre a base da rodada 1

| Área | Antes | Agora |
|---|---|---|
| Atributos com aspas escapadas | 31 ocorrências de `&quot;`/`&#34;` em atributos `aria-*` e `alt` nos templates | aspas corrigidas; teste de regressão `test_html_sem_aspas_escapadas` impede retorno |
| Skip link | alvo `#conteudo-principal` sem `tabindex`, foco programático perdido | `tabindex="-1"` no `<main>`; atalho funciona de fato com teclado |
| Gráficos Chart.js | animação contínua de pulsação em todas as preferências de movimento | `prefers-reduced-motion` desativa a pulsação e reduz as demais animações |
| Tendências dos gráficos | setas ↑/↓ visuais sem descrição para leitor de tela | descrição textual da tendência (`aria-describedby`) em cada métrica |
| Overlay de carregamento | `<main>` sem estado durante o carregamento | `aria-busy="true"` no `<main>` enquanto o overlay está visível |
| Fechamento de modais | foco perdido ao fechar (retornava ao topo da página) | foco devolvido ao gatilho ao fechar, inclusive por `Esc` (Bootstrap) |
| Modal de pagamento (`/financeiro`) | um modal por mensalidade — HTML repetido e 56 consultas por página | modal único preenchido por JS + `joinedload(Mensalidade.pagamentos)`; alerta de desempenho eliminado |
| Macros `_a11y.html` | `|safe` e montagem de atributos por concatenação | macros sem `|safe` e sem `atributos`; HTML gerado escapado e explícito |
| Regiões roláveis (axe-core) | `.table-responsive` sem foco por teclado — conteúdo excedente inalcançável em telas estreitas (violação `scrollable-region-focusable`, WCAG 2.1.1) | `tabindex="0"` nas 12 regiões roláveis; teste de regressão `test_regioes_rolaveis_tem_acesso_por_teclado` |
| Auditoria dinâmica | nenhuma — suíte estrutural não vê contraste computado, ordem real de cabeçalhos nem violações de interação | axe-core 4.10.2 via Playwright em `.github/auditoria/` + workflow de CI (ver §3) |

## 2. Contraste medido

Calculado sobre as cores efetivamente usadas nas telas (luminância relativa
WCAG). Mínimos: 4,5:1 para texto, 3:1 para elementos não textuais.

| Combinação | Razão | Situação |
|---|---|---|
| Adimplente `#1a7f37` sobre branco | 5,08 | passa (era `green`) |
| Inadimplente `#c1121f` sobre branco | 6,22 | **corrigido** (`red` puro dava 4,00) |
| Métrica de atenção `#8a6100` sobre branco | 5,54 | **corrigido** (`.text-warning` dava 1,63) |
| Grade de ocupação, célula mais cheia, texto escuro | 5,53 | **corrigido** (texto branco dava 1,87) |
| Risco baixo `#0f5132` sobre `#d1e7dd` | 7,21 | passa |
| Risco médio `#664d03` sobre `#fff3cd` | 7,21 | passa |
| Risco alto `#842029` sobre `#f8d7da` | 7,08 | passa |
| Contorno de foco `#0a58ca` | 6,44 | passa (mínimo 3:1) |
| `skip-link` `#0a58ca` sobre branco | 6,44 | passa |
| `text-muted` `#6c757d` sobre branco | 4,69 | passa, com folga pequena |
| `text-success` / `text-danger` / `text-primary` | 4,53 / 4,53 / 4,50 | passam no limite |

Observações:

- `text-primary` e `bg-primary` ficam exatamente em 4,50:1. Qualquer escurecimento
  do fundo ou mudança de tema pode derrubá-los; convém não mexer nesses tons sem
  medir de novo.
- `#ffc107` permanece em uso apenas em **ícones decorativos** (`aria-hidden="true"`,
  sempre acompanhados de texto) e em `badge text-bg-warning`, que o Bootstrap
  renderiza com texto escuro. Nenhum texto depende mais dessa cor.

## 3. Verificação automatizada

```powershell
pip install -r requirements-dev.txt
pytest tests/test_acessibilidade.py
```

119 testes, cobrindo por rota e por perfil: `<main>` único, skip link como
primeiro focável, `<h1>` único, hierarquia sem saltos, `aria-current`, rótulo
associado em todo campo visível, ids únicos, ausência de `name` repetido,
indicação textual de obrigatoriedade, erro associado ao campo, valores
preservados, nome acessível em botões e links, ícones decorativos ocultos,
`aria-labelledby` em modais, `caption`/`scope` em tabelas, `role="img"` e tabela
equivalente nos gráficos, `role` das mensagens, `aria-live`, paginação, ausência
de `tabindex` positivo e acesso por teclado nas regiões roláveis (rodada 2).

### Rodada 3 (v1) — acessibilidade portada sobre a base funcional da main

Esta rodada (branch `feature/paulo-acessibilidade-v1`) parte da
`feature/paulo-acessibilidade` (= `main`), que já traz as funcionalidades do
grupo (painel do aluno na rota `dashboard`, restrição do perfil professora aos
seus alunos, troca de senha, cor/emoji por instrumento), e aplica por cima a
camada de acessibilidade da branch `feature/paulo-acesssibilidade-kiro2`.

Princípio do porte: **a kiro2 é o modelo de acessibilidade; a main é a autoridade
de funcionalidade.** Onde a decisão de acessibilidade da kiro2 removeria uma
função da main, prevalece a main. O caso concreto é o Dashboard: a kiro2 o havia
tornado exclusivo da gestora; aqui ele **permanece multi-perfil** (gestora vê o
"Painel Gerencial"; professora e aluno veem "Meu Painel"/painel do aluno), e a
acessibilidade é aplicada dentro dele sem mudar quem o acessa.

| Área | Antes | Agora |
|---|---|---|
| Acesso ao Dashboard | rota multi-perfil da main (gestora, professora e aluno), com métricas em `<h2>` e ícones sem `aria-hidden` | **mantido multi-perfil**; acessibilidade aplicada: `<h1>` único, métricas em `<p class="h2">`, ícones decorativos com `aria-hidden`, gráficos com `role="img"` + tabela + descrição de tendência |
| Overlay de carregamento | `aria-busy` no `<main>`; foco na mensagem | além disso, o fundo (`#app-layout`) fica `inert` + `aria-hidden` enquanto o overlay está visível, com remoção garantida e salvaguarda por timeout |
| Contador de resultados (financeiro) | texto estático | região `aria-live="polite"` |
| Tabela de pagamentos (modal único) | preenchida por JS sem anúncio | wrapper `aria-live="polite"`, anuncia pagamentos ou estado vazio |
| `autocomplete` | login, usuário e registro já cobertos na rodada 2 | acrescentado `name` no nome do aluno e `street-address` no endereço |
| Foco no resumo de erros | modal reabre e foca o resumo (rodada 2) | mantido; salvaguarda para resumo renderizado fora de modal (formulário em página inteira) |

130 testes estruturais de acessibilidade: gestora e professora com o item
"Dashboard" no menu, aluno sem o item (mas com acesso ao próprio painel pela
rota), inertização do fundo sob carregamento, `aria-live` no contador e na tabela
de pagamentos, `autocomplete` em login/usuário/aluno e foco no resumo de erros.
A suíte completa do projeto passa (261 testes, 1 pulado): a acessibilidade
conviveu com as funcionalidades da main (perfil professora, painel do aluno,
troca de senha) sem regressão.

O que a rodada 3 **não** cobre (segue dependente de validação manual): qualidade
dos nomes acessíveis, contraste em hover/foco/desabilitado, zoom 200%/400%,
viewport de 320 px, alvo de toque de 24×24 px e a experiência real com leitor de
tela.

### Estado da suíte completa

`pytest` na branch `feature/paulo-acessibilidade-v1` → **261 passam, 1 pulado,
0 falham**. Todas as funcionalidades da main (perfil professora, painel do aluno,
troca de senha, cor/emoji por instrumento) continuam funcionando sobre a
acessibilidade portada.

Os testes que o grupo escreveu para a estrutura antiga das métricas do dashboard
(`<h2 class="text-...">N</h2>`) foram atualizados para a nova marcação acessível
(`<p class="h2 ...">N</p>`), já que números deixaram de ser títulos de seção. A
regra de negócio verificada continua a mesma; só mudou o seletor.

### Auditoria automatizada com axe-core (opcional)

A suíte pytest acima verifica estrutura (landmarks, rótulos, nomes
acessíveis). Um segundo nível de auditoria — regras dinâmicas do axe-core,
como contraste computado e ordem real de cabeçalhos — fica em
`.github/auditoria/`:

```powershell
# terminal 1: aplicação com banco de dados de exemplo (porta 5001)
pip install -r requirements.txt
python .github/auditoria/servidor.py

# terminal 2: auditoria (requer Node 20+)
cd .github/auditoria
npm install
npx playwright install chromium
npm run auditar
```

O mesmo conjunto roda em `.github/workflows/acessibilidade.yml` (push e pull
request para `main`): sobe o servidor de teste, percorre as rotas com
Playwright (perfis gestora e aluna) e **falha o job se houver qualquer
violação** das tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` e `wcag22aa`.

> **Governança:** a pasta `.github/` é abrangida pela regra `.*/` do
> `.gitignore`. Sem uma exceção explícita nesse arquivo, o workflow **não é
> versionado** — ele existe apenas em cópias locais, como as demais pastas
> de pontos. Habilitá-lo no repositório é decisão da equipe.

> **Execução local (26/09/2026):** a primeira passagem, em 5 rotas como smoke
> test, encontrou 1 violação sistêmica — `scrollable-region-focusable` (grave,
> WCAG 2.1.1) nas `.table-responsive` de `/inicio`, `/dashboard` e `/minha-area`
> (12 ocorrências em 9 templates). `/login` e `/financeiro` saíram limpas.
>
> **A violação foi corrigida no mesmo dia** (`tabindex="0"` nas 12 regiões
> roláveis + teste de regressão na suíte estrutural) e a auditoria foi refeita
> em navegador real: `/login`, `/inicio`, `/dashboard`, `/financeiro` e
> `/minha-area` saíram com **0 violações** (22 a 28 regras passando por página).
> O contraste continuou como "incomplete" nas páginas da gestora (o axe não
> computou todo o texto; requer a revisão manual prevista na §4).
>
> O job pode ser habilitado no repositório remoto assim que a equipe decidir a
> exceção de versionamento da `.github/` (nota de governança acima).

## 4. O que NÃO foi verificado

Nenhum item abaixo pode ser considerado atendido com base no que foi feito até
aqui. Todos exigem execução manual por uma pessoa.

| Item | Critério WCAG | Como verificar |
|---|---|---|
| Ordem de foco e ausência de armadilhas | 2.4.3, 2.1.2 | `Tab`/`Shift+Tab` em cada fluxo: login, cadastro de aluno, quitação, acompanhamento, exclusão, área do aluno |
| Qualidade dos nomes acessíveis | 4.1.2 | ouvir os rótulos no NVDA; o teste garante presença, não utilidade |
| Anúncio real de erros e status | 4.1.3 | submeter formulário inválido com NVDA ligado |
| Foco ao reabrir modal com erro | 2.4.3 | validar que o foco cai no resumo e que `Esc` devolve ao gatilho |
| Contraste em hover, foco e desabilitado | 1.4.11 | inspecionar estados no navegador; só o estado normal foi medido |
| Zoom de 200% e 400% | 1.4.4, 1.4.10 | verificar perda de conteúdo e rolagem em dois eixos |
| Viewport de 320 px | 1.4.10 | sidebar off-canvas, tabelas e modais |
| Leitura das tabelas equivalentes | 1.1.1 | conferir no NVDA se `<details>` é encontrado e navegável |
| Grade de ocupação com leitor de tela | 1.3.1 | navegar célula a célula e confirmar que dia e hora são anunciados |
| Alvo de clique de 24x24 px | 2.5.8 | medir os botões de ação em tabela no navegador |

### Roteiro sugerido para a validação manual

1. Navegador com NVDA (Windows) ou Orca (Linux), teclado apenas.
2. Percorrer: `/login` → `/inicio` → `/alunos` (cadastrar com erro, depois
   corrigir) → `/aluno/<id>` → `/financeiro` (registrar quitação) →
   `/acompanhamento` → `/relatorios` → `/instrumentos` → `/usuarios`.
3. Repetir o fluxo do aluno em `/minha-area`.
4. Repetir com zoom em 200% e 400%.
5. Registrar cada achado nesta tabela, com data e responsável.

| Data | Quem | Item verificado | Resultado |
|---|---|---|---|
| | | | |

## 5. Dívidas conhecidas

- `title="Recolher/expandir menu"` foi removido em favor de `aria-label`
  dinâmico; a dica visual de mouse deixou de existir nesse botão.
- O overlay de carregamento (`#spinner-overlay`) marca o `<main>` com
  `aria-busy="true"` e devolve o foco ao gatilho ao ocultar, mas não torna o
  restante da página inerte — com o overlay visível, `Tab` ainda alcança
  conteúdo de fundo.
- Retorno de foco em modais: os gatilhos usam `data-bs-toggle="modal"`
  (o Bootstrap devolve o foco ao gatilho ao fechar, inclusive por `Esc`), e o
  fluxo de reabertura server-side (`data-abrir-modal`) move o foco para o
  resumo de erros. **Justificativa da dívida:** isso foi verificado apenas por
  inspeção estática do HTML e do JS — não há teste de interação com Playwright
  (o pacote não está instalado no ambiente) para fechamento por `Esc`. O caso
  fica anotado como débito a validar manualmente no roteiro da §4, linha "Foco
  ao reabrir modal com erro".

"""
Treina o modelo de risco de evasão e exporta para app/modelo_evasao.json.

Uso (dentro de sisviolin/, com o venv e ``pip install -r requirements-ml.txt``):

    python -m ml.treinar                  # avalia, exporta o JSON e escreve instance/ml/
    python -m ml.treinar --sem-exportar   # só avalia (não mexe em app/modelo_evasao.json)
    python -m ml.treinar --saida pasta    # onde gravar base e métricas (padrão instance/ml)

Tudo sai do banco da aplicação (o mesmo que o app usa: SQLite local ou
``DATABASE_URL``): o rótulo por aluno-ano vem das mensalidades (ver
ml/rotulos.py) e as features da mesma ``app.risco.caracteristicas`` que o app
usa, com o corte em 31/12 de cada ano letivo. Compara regressão logística,
random forest e a regra ``RISK_CONFIG`` por validação cruzada estratificada
(5 dobras, agrupadas por aluno) e por validação temporal (treina até 2024,
testa 2025). Só a regressão logística é exportada: ela roda em produção sem
scikit-learn (app/risco.py).

Nada é gravado no banco.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import create_app, db  # noqa: E402
from app.models import Aluno  # noqa: E402
from app.risco import FEATURES, caracteristicas, carregar_historico, pontuar, regra  # noqa: E402
from ml.rotulos import rotulos_aluno_ano  # noqa: E402

SEMENTE = 42
DOBRAS = 5
ANO_TESTE_TEMPORAL = 2025      # treina com anos anteriores, testa neste
LIMIAR_MEDIO, LIMIAR_ALTO = 0.20, 0.50   # = RISK_CONFIG limiar_medio/alto ÷ 100


# ---------------------------------------------------------------------------
# Base de treino
# ---------------------------------------------------------------------------
def montar_base(app, hoje):
    """DataFrame com uma linha por aluno-ano: rótulo, corte, features e regra."""
    descartes = Counter()
    linhas = []
    with app.app_context():
        cfg = app.config['RISK_CONFIG']
        rotulos = rotulos_aluno_ano(hoje)
        alunos = {a.id: a for a in Aluno.query.all()}
        historico = carregar_historico()
        for r in rotulos:
            aluno = alunos[r['aluno_id']]
            mens, aulas = historico.get(aluno.id, ([], []))
            feats = caracteristicas(aluno, mens, aulas, r['corte'])
            if feats['n_mens'] == 0 and feats['n_aulas'] == 0:
                descartes['sem histórico antes do corte'] += 1   # não deve ocorrer
                continue
            linhas.append({**r, **feats, 'regra': regra(feats, cfg)})
    base = pd.DataFrame(linhas)
    return base, descartes, len(rotulos)


# ---------------------------------------------------------------------------
# Avaliação
# ---------------------------------------------------------------------------
def modelos():
    # C=0,05: regularização forte — ~55 positivos e features colineares
    # (n_mens, n_atraso, tempo de casa); em CV agrupada e temporal deu AUC igual
    # ou melhor que C=1 com coeficientes menores e sinais mais estáveis.
    return {
        'regressao_logistica': make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.05, class_weight='balanced', max_iter=5000, random_state=SEMENTE)),
        'random_forest': RandomForestClassifier(
            n_estimators=400, min_samples_leaf=5, class_weight='balanced_subsample',
            random_state=SEMENTE, n_jobs=-1),
    }


def metricas(y, proba):
    """AUC, average precision e, nos limiares do app (médio 0,20 / alto 0,50),
    recall, precisão e matriz de confusão da classe 'evadiu'."""
    m = {'auc': roc_auc_score(y, proba), 'ap': average_precision_score(y, proba)}
    for nome, limiar in (('medio', LIMIAR_MEDIO), ('alto', LIMIAR_ALTO)):
        pred = (proba >= limiar).astype(int)
        tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
        m[nome] = {'limiar': limiar,
                   'recall': recall_score(y, pred, zero_division=0),
                   'precisao': precision_score(y, pred, zero_division=0),
                   'sinalizados': int(pred.sum()), 'tn': int(tn), 'fp': int(fp),
                   'fn': int(fn), 'tp': int(tp)}
    return m


def intervalo_auc(y, proba, n=1000, semente=SEMENTE):
    """Intervalo de 95 % da AUC por bootstrap (reamostra as linhas)."""
    rng = np.random.default_rng(semente)
    y = np.asarray(y)
    proba = np.asarray(proba)
    aucs = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max():
            continue
        aucs.append(roc_auc_score(y[idx], proba[idx]))
    return float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))


def avaliar(base):
    X = base[FEATURES].to_numpy(dtype=float)
    y = base['evadiu'].to_numpy(dtype=int)
    # Dobras agrupadas por aluno: o mesmo aluno aparece em vários anos com
    # features quase iguais (idade, valor, tempo de casa); se um ano dele cai
    # no treino e outro no teste, o modelo "reconhece" o aluno e a AUC infla.
    grupos = base['aluno_id'].to_numpy()
    cv = StratifiedGroupKFold(DOBRAS, shuffle=True, random_state=SEMENTE)

    resultado = {'validacao_cruzada': {}, 'temporal': {}, 'coeficientes': {}, 'importancias': {}}

    # Regra: não precisa de validação, não aprende nada
    proba_regra = base['regra'].to_numpy(dtype=float) / 100.0
    resultado['validacao_cruzada']['regra'] = metricas(y, proba_regra)
    resultado['validacao_cruzada']['regra']['ic_auc'] = intervalo_auc(y, proba_regra)

    probas_cv = {}
    for nome, est in modelos().items():
        proba = cross_val_predict(est, X, y, cv=cv, groups=grupos, method='predict_proba')[:, 1]
        probas_cv[nome] = proba
        resultado['validacao_cruzada'][nome] = metricas(y, proba)
        resultado['validacao_cruzada'][nome]['ic_auc'] = intervalo_auc(y, proba)

    # Validação temporal: o que o modelo diria no fim de 2024 sobre 2025
    treino = base['ano'] < ANO_TESTE_TEMPORAL
    teste = base['ano'] == ANO_TESTE_TEMPORAL
    if teste.sum() and y[teste].min() != y[teste].max():
        resultado['temporal']['n_treino'] = int(treino.sum())
        resultado['temporal']['n_teste'] = int(teste.sum())
        resultado['temporal']['positivos_teste'] = int(y[teste].sum())
        resultado['temporal']['regra'] = metricas(y[teste], proba_regra[teste])
        for nome, est in modelos().items():
            est.fit(X[treino], y[treino])
            resultado['temporal'][nome] = metricas(y[teste], est.predict_proba(X[teste])[:, 1])

    # Modelo final (toda a base) — coeficientes e importâncias
    final = modelos()
    lr = final['regressao_logistica'].fit(X, y)
    rf = final['random_forest'].fit(X, y)
    resultado['coeficientes'] = dict(zip(FEATURES, lr.named_steps['logisticregression'].coef_[0].tolist()))
    resultado['importancias'] = dict(zip(FEATURES, rf.feature_importances_.tolist()))
    return resultado, lr, probas_cv


def exportar_json(lr, base, resultado, hoje):
    scaler = lr.named_steps['standardscaler']
    reg = lr.named_steps['logisticregression']
    cv = resultado['validacao_cruzada']
    return {
        'versao': 1,
        'treinado_em': hoje.isoformat(),
        'algoritmo': 'StandardScaler + LogisticRegression(C=0.05, class_weight=balanced)',
        'features': list(FEATURES),
        'media': scaler.mean_.tolist(),
        'desvio': scaler.scale_.tolist(),      # sklearn já troca desvio 0 por 1
        'coef': reg.coef_[0].tolist(),
        'intercepto': float(reg.intercept_[0]),
        'base': {'linhas': int(len(base)), 'positivos': int(base['evadiu'].sum()),
                 'alunos': int(base['aluno_id'].nunique()),
                 'anos': [int(a) for a in sorted(base['ano'].unique())]},
        'metricas_cv': {
            'auc': cv['regressao_logistica']['auc'],
            'ic_auc': cv['regressao_logistica']['ic_auc'],
            'auc_regra': cv['regra']['auc'],
            'recall_alto': cv['regressao_logistica']['alto']['recall'],
            'precisao_alto': cv['regressao_logistica']['alto']['precisao'],
        },
    }


# ---------------------------------------------------------------------------
# Impacto no app: como ficam os alunos ativos hoje, regra x modelo
# ---------------------------------------------------------------------------
def impacto_no_app(app, modelo):
    with app.app_context():
        cfg = app.config['RISK_CONFIG']
        ativos = Aluno.query.filter_by(status='ativo').all()
        faixa = lambda s: 'alto' if s >= cfg['limiar_alto'] else 'médio' if s >= cfg['limiar_medio'] else 'baixo'
        pontuar(ativos, modelo=None)            # marca _risco = None -> regra
        regra_faixas = Counter(faixa(a.risco_score) for a in ativos)
        for a in ativos:
            del a._risco
        pontuar(ativos, modelo=modelo)
        modelo_faixas = Counter(faixa(a.risco_score) for a in ativos)
        motivos = Counter(m for a in ativos for m in a._risco['motivos'])
    return {'ativos': len(ativos), 'regra': dict(regra_faixas), 'modelo': dict(modelo_faixas),
            'motivos_mais_comuns': motivos.most_common(8)}


# ---------------------------------------------------------------------------
# Relatório em texto
# ---------------------------------------------------------------------------
def pct(x):
    return f'{100 * x:.0f} %'


def linha_metricas(nome, m):
    a, md = m['alto'], m['medio']
    ic = m.get('ic_auc')
    ic_txt = f' [{ic[0]:.2f}–{ic[1]:.2f}]' if ic else ''
    return (f'| {nome} | {m["auc"]:.3f}{ic_txt} | {m["ap"]:.3f} | '
            f'{pct(md["recall"])} / {pct(md["precisao"])} ({md["sinalizados"]}) | '
            f'{pct(a["recall"])} / {pct(a["precisao"])} ({a["sinalizados"]}) |')


def escrever_relatorio(caminho, base, descartes, total_rotulos, resultado, impacto, hoje):
    n = len(base)
    pos = int(base['evadiu'].sum())
    por_ano = base.groupby('ano')['evadiu'].agg(['count', 'sum'])
    por_motivo = base['motivo'].value_counts()
    cv, temp = resultado['validacao_cruzada'], resultado['temporal']

    L = []
    L.append(f'# Modelo de risco de evasão — métricas ({hoje.strftime("%d/%m/%Y")})\n')
    L.append('Gerado por `python -m ml.treinar` a partir do banco da aplicação (dados '
             'importados de um proxy anonimizado de ERP escolar). Uma linha por aluno-ano: em '
             '31/12 de cada ano em que o aluno teve mensalidade, com as features calculadas só '
             'com o histórico até essa data, o rótulo diz se o aluno **não continuou no ano '
             'seguinte** (nenhuma mensalidade em Y+1; no ano corrente, cadastro inativo).\n')
    L.append('## Base de treino\n')
    L.append(f'- {n} linhas aluno-ano ({base["aluno_id"].nunique()} alunos, anos '
             f'{int(base["ano"].min())}–{int(base["ano"].max())}); **{pos} positivos '
             f'({pct(pos / n)})**.')
    L.append(f'- Rótulos gerados: {total_rotulos}; descartados: '
             + (', '.join(f'{v} {k}' for k, v in descartes.items()) or 'nenhum') + '.')
    L.append('- Por motivo do rótulo: ' + ', '.join(f'{k} {v}' for k, v in por_motivo.items()) + '.')
    L.append('\n| Ano | Linhas | Evadiu |\n|---|---|---|')
    for ano, r in por_ano.iterrows():
        L.append(f'| {ano} | {int(r["count"])} | {int(r["sum"])} ({pct(r["sum"] / r["count"])}) |')

    L.append('\n## Validação cruzada estratificada (5 dobras, agrupadas por aluno)\n')
    L.append('Todos os anos de um mesmo aluno ficam na mesma dobra (sem isso a random forest '
             '"reconhece" o aluno e a AUC sobe artificialmente). Recall / precisão da classe '
             '"evadiu" nos limiares do app: **médio** (score ≥ 20) e **alto** (score ≥ 50); '
             'entre parênteses, quantos alunos-ano ficam sinalizados. AUC com intervalo de '
             '95 % por bootstrap.\n')
    L.append('| Método | AUC | AP | médio: recall / precisão (n) | alto: recall / precisão (n) |')
    L.append('|---|---|---|---|---|')
    L.append(linha_metricas('Regra `RISK_CONFIG`', cv['regra']))
    L.append(linha_metricas('Regressão logística (exportada)', cv['regressao_logistica']))
    L.append(linha_metricas('Random forest (só comparação)', cv['random_forest']))
    L.append(f'\nBase de comparação: sinalizar todo mundo dá recall 100 % com precisão {pct(pos / n)}; '
             'AUC 0,5 é o acaso.')

    if temp:
        L.append(f'\n## Validação temporal (treina {int(base["ano"].min())}–{ANO_TESTE_TEMPORAL - 1}, '
                 f'testa {ANO_TESTE_TEMPORAL})\n')
        L.append(f'{temp["n_treino"]} linhas de treino, {temp["n_teste"]} de teste '
                 f'({temp["positivos_teste"]} positivos).\n')
        L.append('| Método | AUC | AP | médio: recall / precisão (n) | alto: recall / precisão (n) |')
        L.append('|---|---|---|---|---|')
        L.append(linha_metricas('Regra `RISK_CONFIG`', temp['regra']))
        L.append(linha_metricas('Regressão logística', temp['regressao_logistica']))
        L.append(linha_metricas('Random forest', temp['random_forest']))

    L.append('\n## O que pesa no modelo exportado\n')
    L.append('Coeficientes da regressão logística sobre features padronizadas (positivo = '
             'empurra para "evadiu"); ao lado, a importância na random forest.\n')
    L.append('| Feature | Coef. (LR) | Import. (RF) | Média na base |')
    L.append('|---|---|---|---|')
    for f in sorted(FEATURES, key=lambda f: -abs(resultado['coeficientes'][f])):
        L.append(f'| `{f}` | {resultado["coeficientes"][f]:+.3f} | '
                 f'{resultado["importancias"][f]:.3f} | {base[f].mean():.2f} |')

    L.append('\n## Impacto no app (alunos ativos hoje)\n')
    L.append(f'{impacto["ativos"]} alunos ativos. Faixas de risco:\n')
    L.append('| | baixo | médio | alto |\n|---|---|---|---|')
    for nome in ('regra', 'modelo'):
        f = impacto[nome]
        L.append(f'| {nome} | {f.get("baixo", 0)} | {f.get("médio", 0)} | {f.get("alto", 0)} |')
    L.append('\nMotivos mais citados pelo modelo: '
             + '; '.join(f'{m} ({c})' for m, c in impacto['motivos_mais_comuns']) + '.')

    L.append('\n## Limitações (para o relatório)\n')
    L.append('- Os dados vieram de uma **demonstração anonimizada de ERP de escola regular**, usada '
             'como proxy — não é a operação do Studio Duo. Curso/série viraram instrumento na '
             'importação; o rótulo (mensalidade no ano seguinte) coincide em 92 % com a situação '
             'de matrícula do ERP.')
    L.append('- 94 % das mensalidades estão sem pagamento; por isso o alvo é **evasão** (matrícula), '
             'não inadimplência, e as features financeiras discriminam pouco.')
    L.append('- Só 45 % dos alunos têm aula registrada; as features pedagógicas são esparsas.')
    L.append(f'- {pos} positivos é pouco: os intervalos da AUC são largos e o resultado deve ser '
             'lido como prova de conceito do pipeline (rótulo → corte temporal → features → '
             'modelo → app), a ser re-treinado com dados reais da escola — o treino lê só o '
             'banco da aplicação, então basta rodar `python -m ml.treinar` de novo.')
    L.append('- O ano corrente é censurado (não se sabe quem volta no próximo); os alunos ativos '
             'hoje são pontuados, não usados no treino.')

    caminho.write_text('\n'.join(L) + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--saida', default=str(RAIZ / 'instance' / 'ml'),
                        help='pasta para base_treino.csv e metricas.md (padrão instance/ml)')
    parser.add_argument('--modelo', default=str(RAIZ / 'app' / 'modelo_evasao.json'),
                        help='onde gravar o JSON do modelo (padrão app/modelo_evasao.json)')
    parser.add_argument('--sem-exportar', action='store_true', help='não grava o JSON do modelo')
    args = parser.parse_args(argv)

    hoje = date.today()
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)

    app = create_app({'MODELO_EVASAO': None})
    base, descartes, total_rotulos = montar_base(app, hoje)
    if base.empty or base['evadiu'].nunique() < 2:
        sys.exit('Base vazia ou sem as duas classes — nada a treinar.')
    base.to_csv(saida / 'base_treino.csv', index=False)
    print(f'Base: {len(base)} linhas aluno-ano, {int(base["evadiu"].sum())} positivos '
          f'({100 * base["evadiu"].mean():.1f} %); descartes: {dict(descartes)}')

    resultado, lr, _ = avaliar(base)
    modelo = exportar_json(lr, base, resultado, hoje)
    impacto = impacto_no_app(app, modelo)
    escrever_relatorio(saida / 'metricas.md', base, descartes, total_rotulos, resultado, impacto, hoje)

    cv = resultado['validacao_cruzada']
    for nome in ('regra', 'regressao_logistica', 'random_forest'):
        m = cv[nome]
        print(f'{nome:22s} AUC {m["auc"]:.3f} [{m["ic_auc"][0]:.2f}–{m["ic_auc"][1]:.2f}]  '
              f'AP {m["ap"]:.3f}  alto: recall {m["alto"]["recall"]:.2f} precisão {m["alto"]["precisao"]:.2f}')
    if resultado['temporal']:
        t = resultado['temporal']
        print(f'temporal ({ANO_TESTE_TEMPORAL}): regra AUC {t["regra"]["auc"]:.3f} · '
              f'LR AUC {t["regressao_logistica"]["auc"]:.3f} · RF AUC {t["random_forest"]["auc"]:.3f}')
    print(f'impacto no app: regra {impacto["regra"]} · modelo {impacto["modelo"]}')
    print(f'relatório: {saida / "metricas.md"}')

    if not args.sem_exportar:
        Path(args.modelo).write_text(json.dumps(modelo, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'modelo exportado: {args.modelo}')


if __name__ == '__main__':
    main()

# ⚡ SpeedLearning

Site em Python (Flask) que interpreta um PDF e o transforma em material de estudo:

1. **Resumos** — o documento é dividido em secções e cada uma recebe um resumo e pontos-chave.
2. **Perguntas de avaliação** — depois de ler cada secção, o utilizador responde a perguntas de
   escolha múltipla e recebe a correção com explicação.
3. **Teste final** — um teste de escolha múltipla sobre todos os assuntos do PDF, com nota final.

## Como executar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py            # abre http://localhost:5000
```

### Modo IA (recomendado)

Com uma chave da API da Anthropic, os resumos e as perguntas são gerados pelo Claude
(`claude-opus-5-5`), com perguntas de compreensão e explicações:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

O pedido usa `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`): se o modelo recusar
o pedido, a API repete-o automaticamente noutro modelo.

### Modo local

Sem chave (ou escolhendo "Modo local" no formulário) o site funciona offline: resumo extrativo
por frequência de termos e perguntas de "completar a frase" com distratores retirados do próprio
documento. Se a API falhar, a aplicação recorre automaticamente a este modo.

## Estrutura

```
app.py                       rotas Flask (upload, secções, teste final) e correção
speedlearning/pdf_reader.py  extração de texto (pypdf) e divisão em secções
speedlearning/generator.py   geradores Claude e local
speedlearning/store.py       cursos guardados em instance/courses/*.json
templates/, static/          interface
tests/                       testes (pytest)
```

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

## Notas

- PDFs digitalizados (só imagem) não têm texto extraível e são rejeitados.
- Limite de upload: 50 MB.
- Em produção define `SECRET_KEY` e usa um servidor WSGI (ex.: `gunicorn app:app`).

"""Geração de resumos e perguntas de escolha múltipla.

Há dois geradores:

* ``ClaudeGenerator`` usa a API da Anthropic (Claude) e produz resumos e
  perguntas de qualidade. É usado quando há credenciais configuradas
  (``ANTHROPIC_API_KEY``).
* ``LocalGenerator`` funciona sem rede: resumo extrativo por frequência de
  termos e perguntas de "preencher o espaço" com distratores tirados do
  próprio documento.

Ambos devolvem um curso no formato::

    {
      "title": str,
      "generator": "claude" | "local",
      "sections": [
        {"title": str, "summary": str, "key_points": [str],
         "questions": [Question]}
      ],
      "final_test": [Question]
    }

    Question = {"question": str, "options": [str], "answer": int,
                "explanation": str}
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from collections import Counter

from .pdf_reader import Section

log = logging.getLogger(__name__)

QUESTIONS_PER_SECTION = 3
FINAL_TEST_SIZE = 10
# Acima disto o texto não é enviado de uma só vez ao modelo.
MAX_CLAUDE_CHARS = 1_500_000


class GenerationError(Exception):
    pass


def generate_course(title: str, sections: list[Section], prefer_claude: bool = True) -> dict:
    """Gera o curso com Claude quando disponível; caso contrário, localmente."""
    if prefer_claude and claude_available():
        try:
            return ClaudeGenerator().generate(title, sections)
        except GenerationError as exc:
            log.warning("Claude indisponível, a usar gerador local: %s", exc)
    return LocalGenerator().generate(title, sections)


def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "answer": {"type": "integer", "description": "Índice (0-3) da opção correta"},
        "explanation": {"type": "string"},
    },
    "required": ["question", "options", "answer", "explanation"],
    "additionalProperties": False,
}

_COURSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}},
                    "questions": {"type": "array", "items": _QUESTION_SCHEMA},
                },
                "required": ["title", "summary", "key_points", "questions"],
                "additionalProperties": False,
            },
        },
        "final_test": {"type": "array", "items": _QUESTION_SCHEMA},
    },
    "required": ["title", "sections", "final_test"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = f"""\
És um tutor que transforma documentos em material de estudo rápido.

Recebes o texto extraído de um PDF, já dividido em partes numeradas. Para cada \
parte, por ordem, cria uma secção com:
- um título curto e descritivo;
- um resumo claro (1 a 3 parágrafos) com as ideias essenciais;
- 3 a 6 pontos-chave curtos;
- {QUESTIONS_PER_SECTION} perguntas de escolha múltipla sobre essa parte.

No fim cria um teste final com {FINAL_TEST_SIZE} perguntas de escolha múltipla \
que cubra todos os assuntos do documento, sem repetir as perguntas das secções \
e privilegiando a compreensão em vez da memorização literal.

Cada pergunta tem exatamente 4 opções plausíveis, uma única correta (campo \
"answer" com o índice 0-3, variando a posição da resposta certa) e uma \
explicação breve que justifica a resposta com base no documento.

Usa apenas informação presente no documento. Escreve tudo na língua do \
documento; se não for clara, usa português de Portugal."""


class ClaudeGenerator:
    model = "claude-opus-5-5"

    def __init__(self, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def generate(self, title: str, sections: list[Section]) -> dict:
        import anthropic

        parts = "\n\n".join(
            f'<parte numero="{i}">\n{s.text}\n</parte>' for i, s in enumerate(sections, 1)
        )
        if len(parts) > MAX_CLAUDE_CHARS:
            raise GenerationError("Documento demasiado grande para ser processado de uma vez.")

        prompt = (
            f"Título do documento: {title}\n"
            f"Número de partes: {len(sections)}\n\n{parts}"
        )
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=_SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": _COURSE_SCHEMA},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise GenerationError("Credenciais da API inválidas.") from exc
        except anthropic.RateLimitError as exc:
            raise GenerationError("Limite de pedidos da API atingido.") from exc
        except anthropic.APIStatusError as exc:
            raise GenerationError(f"Erro da API ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise GenerationError("Sem ligação à API.") from exc

        if response.stop_reason == "refusal":
            raise GenerationError("O modelo recusou processar este documento.")
        if response.stop_reason == "max_tokens":
            raise GenerationError("Resposta do modelo truncada.")

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            course = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenerationError("Resposta do modelo não é JSON válido.") from exc

        course = _validate_course(course)
        course["title"] = course.get("title") or title
        course["generator"] = "claude"
        return course


def _validate_course(course: dict) -> dict:
    """Descarta perguntas mal formadas e garante uma estrutura consistente."""

    def valid(q: dict) -> bool:
        opts = q.get("options") or []
        return (
            isinstance(q.get("question"), str)
            and len(opts) >= 2
            and isinstance(q.get("answer"), int)
            and 0 <= q["answer"] < len(opts)
        )

    sections = []
    for s in course.get("sections", []):
        s["questions"] = [q for q in s.get("questions", []) if valid(q)]
        s.setdefault("key_points", [])
        sections.append(s)
    if not sections:
        raise GenerationError("O modelo não devolveu nenhuma secção.")
    course["sections"] = sections
    course["final_test"] = [q for q in course.get("final_test", []) if valid(q)]
    return course


# ---------------------------------------------------------------------------
# Gerador local (sem rede)
# ---------------------------------------------------------------------------

_STOPWORDS = set(
    """
    a à às ao aos as o os um uma uns umas de do da dos das no na nos nas em por
    para pelo pela pelos pelas com sem sob sobre entre até após e ou mas nem que
    se como quando onde qual quais quem cujo cuja porque pois também já ainda
    muito muita muitos muitas mais menos tão tanto tal tais este esta estes
    estas esse essa esses essas aquele aquela aqueles aquelas isto isso aquilo
    ele ela eles elas eu tu nós vós você vocês seu sua seus suas meu minha
    nosso nossa lhe lhes me te nos vos não sim ser é são foi foram era eram
    será serão sendo sido estar está estão estava ter tem têm tinha tinham
    haver há havia pode podem deve devem fazer faz fazem cada todo toda todos
    todas outro outra outros outras mesmo mesma ser só apenas assim então
    the of and to in is are was were be been being for on with as by at from
    that this these those it its or an not but which who what when where how
    can could will would should may might must has have had do does did than
    then there their they them also such into about more most other some any
    each all both only over under between after before during
    """.split()
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]+")


class LocalGenerator:
    def __init__(self, seed: int | None = 42):
        self.rng = random.Random(seed)

    def generate(self, title: str, sections: list[Section]) -> dict:
        all_text = "\n".join(s.text for s in sections)
        doc_freq = _term_frequencies(all_text)
        vocabulary = [w for w, _ in doc_freq.most_common(300)]

        out_sections = []
        final_pool = []
        for section in sections:
            sentences = _sentences(section.text)
            freq = _term_frequencies(section.text)
            ranked = sorted(sentences, key=lambda s: _score(s, freq), reverse=True)

            top = set(ranked[:4])
            summary = " ".join(s for s in sentences if s in top)
            key_points = [w.capitalize() for w, _ in freq.most_common(5)]

            questions = self._questions(ranked, freq, vocabulary, QUESTIONS_PER_SECTION * 2)
            out_sections.append(
                {
                    "title": section.title,
                    "summary": summary or section.text[:600],
                    "key_points": key_points,
                    "questions": questions[:QUESTIONS_PER_SECTION],
                }
            )
            final_pool.extend(questions[QUESTIONS_PER_SECTION:])

        # Teste final: perguntas ainda não vistas, repartidas pelas secções.
        if len(final_pool) < FINAL_TEST_SIZE:
            for s in out_sections:
                final_pool.extend(s["questions"])
        self.rng.shuffle(final_pool)
        final_test = final_pool[:FINAL_TEST_SIZE]

        return {
            "title": title,
            "generator": "local",
            "sections": out_sections,
            "final_test": final_test,
        }

    def _questions(self, ranked, freq, vocabulary, limit):
        questions = []
        used_terms = set()
        for sentence in ranked:
            if len(questions) >= limit:
                break
            if not 40 <= len(sentence) <= 400:
                continue
            candidates = [
                w for w in _WORD_RE.findall(sentence)
                if w.lower() in freq and w.lower() not in used_terms and len(w) > 4
            ]
            if not candidates:
                continue
            term = max(candidates, key=lambda w: freq[w.lower()])
            distractors = self._distractors(term, sentence, vocabulary)
            if len(distractors) < 3:
                continue
            used_terms.add(term.lower())

            blanked = re.sub(rf"\b{re.escape(term)}\b", "_____", sentence, count=1)
            options = distractors[:3] + [term.lower()]
            self.rng.shuffle(options)
            questions.append(
                {
                    "question": f"Complete a frase: «{blanked}»",
                    "options": options,
                    "answer": options.index(term.lower()),
                    "explanation": f"No documento: «{sentence}»",
                }
            )
        return questions

    def _distractors(self, term, sentence, vocabulary):
        in_sentence = {w.lower() for w in _WORD_RE.findall(sentence)}
        pool = [
            w for w in vocabulary
            if w != term.lower() and w not in in_sentence and len(w) > 3
        ]
        # Preferir termos de tamanho semelhante para serem plausíveis.
        pool.sort(key=lambda w: abs(len(w) - len(term)))
        pool = pool[:12]
        self.rng.shuffle(pool)
        return pool[:3]


def _sentences(text: str) -> list[str]:
    # Linhas curtas sem pontuação final são títulos: não entram nas frases.
    lines = [
        line for line in text.splitlines()
        if re.search(r"[.!?:;]\s*$", line) or len(line.split()) > 8
    ]
    text = re.sub(r"\s+", " ", " ".join(lines))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"«(])", text)
    seen = set()
    sentences = []
    for p in (p.strip() for p in parts):
        key = p.lower().rstrip(".!?")
        # Ignora repetições e fragmentos de frases já vistas (ex.: quebra de página).
        if len(p) > 20 and not any(s.startswith(key) or key.startswith(s) for s in seen):
            seen.add(key)
            sentences.append(p)
    return sentences


def _term_frequencies(text: str) -> Counter:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    return Counter(w for w in words if w not in _STOPWORDS and len(w) > 2)


def _score(sentence: str, freq: Counter) -> float:
    words = [w.lower() for w in _WORD_RE.findall(sentence)]
    if not words:
        return 0.0
    return sum(freq.get(w, 0) for w in words) / (len(words) ** 0.6)

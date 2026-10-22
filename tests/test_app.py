import io
import re
from types import SimpleNamespace

import pytest
from fpdf import FPDF

from app import create_app, grade
from speedlearning.generator import ClaudeGenerator, LocalGenerator
from speedlearning.pdf_reader import PdfError, extract_text, split_sections

TEXT = [
    (
        "A Fotossíntese",
        "A fotossíntese é o processo pelo qual as plantas convertem energia luminosa em "
        "energia química. Este processo ocorre nos cloroplastos, que contêm clorofila. "
        "A clorofila absorve a luz vermelha e azul e reflete a luz verde. Durante a "
        "fotossíntese, a planta consome dióxido de carbono e água e liberta oxigénio. "
        "A glicose produzida é usada como fonte de energia pela planta. ",
    ),
    (
        "A Respiração Celular",
        "A respiração celular ocorre nas mitocôndrias de quase todas as células. Neste "
        "processo, a glicose é degradada na presença de oxigénio para produzir ATP. O ATP "
        "é a principal molécula de energia utilizada pelas células. Como resíduos, a "
        "respiração liberta dióxido de carbono e água. As mitocôndrias são por isso "
        "chamadas as centrais energéticas da célula. ",
    ),
]


def make_pdf(repeat=8) -> bytes:
    pdf = FPDF()
    pdf.set_font("helvetica", size=11)
    for heading, body in TEXT:
        pdf.add_page()
        pdf.multi_cell(0, 6, heading)
        pdf.ln()
        pdf.multi_cell(0, 6, body * repeat)
    return bytes(pdf.output())


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=str(tmp_path), prefer_claude=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_extract_and_split():
    title, pages = extract_text(make_pdf())
    assert len(pages) >= 2
    assert "fotossíntese" in pages[0].lower()
    sections = split_sections(pages)
    assert len(sections) >= 2
    assert sum(len(s.text) for s in sections) > 1000


def test_invalid_pdf():
    with pytest.raises(PdfError):
        extract_text(b"isto nao e um pdf")


def test_local_generator_questions_are_valid():
    _, pages = extract_text(make_pdf())
    course = LocalGenerator().generate("Biologia", split_sections(pages))
    assert course["sections"]
    for sec in course["sections"]:
        assert sec["summary"]
        for q in sec["questions"]:
            assert len(q["options"]) == 4
            assert len(set(q["options"])) == 4
            assert 0 <= q["answer"] < 4
            assert "_____" in q["question"]
    assert course["final_test"]


def test_grade():
    qs = [{"answer": 1, "options": ["a", "b"]}, {"answer": 0, "options": ["a", "b"]}]
    result = grade(qs, {"q0": "1", "q1": "1"})
    assert result["correct"] == 1 and result["percent"] == 50
    assert grade(qs, {})["correct"] == 0


def test_full_flow(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "PDF" in resp.text

    resp = client.post(
        "/upload",
        data={"pdf": (io.BytesIO(make_pdf()), "bio.pdf"), "mode": "local"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    course_url = resp.headers["Location"]
    course_id = course_url.rstrip("/").split("/")[-1]

    resp = client.get(course_url)
    assert "Começar a estudar" in resp.text

    resp = client.get(f"/curso/{course_id}/secao/0")
    assert "Resumo" in resp.text and "Verificar respostas" in resp.text
    n_questions = len(re.findall(r'name="q\d+" value="0"', resp.text))
    answers = {f"q{i}": "0" for i in range(n_questions)}
    resp = client.post(f"/curso/{course_id}/secao/0", data=answers)
    assert "Resultado:" in resp.text

    resp = client.get(f"/curso/{course_id}/teste-final")
    assert "Entregar teste" in resp.text
    resp = client.post(f"/curso/{course_id}/teste-final", data={"q0": "0"})
    assert "Resultado:" in resp.text

    resp = client.get(course_url)
    assert "Teste final:" in resp.text


def test_upload_rejects_non_pdf(client):
    resp = client.post(
        "/upload",
        data={"pdf": (io.BytesIO(b"abc"), "notas.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "tem de ser um PDF" in resp.text


def test_unknown_course(client):
    assert client.get("/curso/../../etc/passwd").status_code == 404
    assert client.get("/curso/" + "0" * 32).status_code == 404


class _FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def test_claude_generator_parses_structured_output():
    payload = (
        '{"title": "Bio", "sections": [{"title": "S1", "summary": "Resumo", '
        '"key_points": ["a"], "questions": [{"question": "Q?", "options": '
        '["a","b","c","d"], "answer": 2, "explanation": "porque"}, {"question": '
        '"Má", "options": ["a"], "answer": 5, "explanation": ""}]}], '
        '"final_test": [{"question": "F?", "options": ["a","b","c","d"], '
        '"answer": 0, "explanation": "x"}]}'
    )
    message = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=payload)]
    )
    calls = {}

    def stream(**kwargs):
        calls.update(kwargs)
        return _FakeStream(message)

    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    _, pages = extract_text(make_pdf())
    course = ClaudeGenerator(client=fake_client).generate("Bio", split_sections(pages))

    assert course["generator"] == "claude"
    assert len(course["sections"][0]["questions"]) == 1  # a pergunta inválida é descartada
    assert course["final_test"][0]["answer"] == 0
    assert calls["model"] == "claude-opus-5-5"
    assert "<parte numero=\"1\">" in calls["messages"][0]["content"]

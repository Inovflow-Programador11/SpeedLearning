"""Extração de texto de PDFs e divisão em secções de estudo."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

# Tamanho alvo (em caracteres) de cada secção de estudo.
TARGET_SECTION_CHARS = 3500
MAX_SECTIONS = 12


class PdfError(Exception):
    """O PDF não pôde ser lido ou não contém texto."""


@dataclass
class Section:
    title: str
    text: str


def extract_text(data: bytes) -> tuple[str, list[str]]:
    """Devolve (título, lista de textos por página)."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [_clean(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pypdf lança vários tipos de erro
        raise PdfError(f"Não foi possível ler o PDF: {exc}") from exc

    if not any(p.strip() for p in pages):
        raise PdfError(
            "O PDF não contém texto extraível (pode ser um documento digitalizado)."
        )

    title = ""
    meta = reader.metadata
    if meta and meta.title:
        title = str(meta.title).strip()
    if not title:
        title = _first_line(pages)
    return title, pages


def split_sections(pages: list[str]) -> list[Section]:
    """Agrupa parágrafos em secções de tamanho semelhante."""
    paragraphs = [
        p.strip()
        for page in pages
        for p in re.split(r"\n\s*\n", page)
        if len(p.strip()) > 1
    ]
    total = sum(len(p) for p in paragraphs)
    n_sections = max(1, min(MAX_SECTIONS, round(total / TARGET_SECTION_CHARS)))
    target = total / n_sections

    chunks: list[list[str]] = [[]]
    size = 0
    for para in paragraphs:
        if size >= target and len(chunks) < n_sections:
            chunks.append([])
            size = 0
        chunks[-1].append(para)
        size += len(para)

    sections = []
    for i, chunk in enumerate(chunks, start=1):
        text = "\n\n".join(chunk)
        sections.append(Section(title=_guess_heading(chunk) or f"Parte {i}", text=text))
    return sections


def _clean(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"-\n(\w)", r"\1", text)  # junta palavras hifenizadas
    text = re.sub(r"[ \t]+", " ", text)
    # Uma linha que não termina em pontuação continua normalmente na seguinte.
    text = re.sub(r"(?<![.!?:;\n])\n(?!\n)(?=[a-zà-ÿ0-9(])", " ", text)
    return text.strip()


def _first_line(pages: list[str]) -> str:
    for page in pages:
        for line in page.splitlines():
            line = line.strip()
            if 3 <= len(line) <= 120:
                return line
    return "Documento"


def _guess_heading(chunk: list[str]) -> str | None:
    first = chunk[0].splitlines()[0].strip() if chunk else ""
    if 3 <= len(first) <= 80 and not first.endswith((".", ",", ";")):
        return first
    return None

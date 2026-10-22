"""Armazenamento simples dos cursos gerados em ficheiros JSON."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class CourseStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, course: dict) -> str:
        course_id = uuid.uuid4().hex
        self._path(course_id).write_text(json.dumps(course, ensure_ascii=False), "utf-8")
        return course_id

    def get(self, course_id: str) -> dict | None:
        if not _ID_RE.match(course_id):
            return None
        path = self._path(course_id)
        if not path.exists():
            return None
        return json.loads(path.read_text("utf-8"))

    def _path(self, course_id: str) -> Path:
        return self.directory / f"{course_id}.json"

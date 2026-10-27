"""Embute a página de estudo (estudo.html) na aplicação (speedlearning.html).

A aplicação usa este modelo para gerar a página de estudo descarregável.
Corre `python artifact/build.py` sempre que alterares estudo.html.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
START, END = "/*ESTUDO_TEMPLATE_START*/", "/*ESTUDO_TEMPLATE_END*/"

estudo = (HERE / "estudo.html").read_text("utf-8")
estudo = re.sub(r"<title>.*?</title>", "<title>__TITLE__</title>", estudo, count=1)
estudo = re.sub(
    r'(<script type="application/json" id="course-data">).*?(</script>)',
    r"\1__COURSE_DATA__\2", estudo, count=1, flags=re.S,
)
literal = json.dumps(estudo, ensure_ascii=False).replace("<", "\\u003c")

app = HERE / "speedlearning.html"
src = app.read_text("utf-8")
block = f"{START}const ESTUDO_TEMPLATE = {literal};{END}"
if START in src:
    src = re.sub(re.escape(START) + ".*?" + re.escape(END), lambda _: block, src, flags=re.S)
else:
    src = src.replace('"use strict";', '"use strict";\n' + block, 1)
app.write_text(src, "utf-8")
print("ok", len(literal))

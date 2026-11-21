"""A sheet as a PDF, to print and work on with a pen: the questions with room to answer, then the
correction on a page of its own, for the parent.

Made by the API from the published sheet, when asked: no file to keep, and every sheet already
online has its PDF. Pure Python (fpdf2) and the DejaVu fonts, which have the accents and the
maths signs.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from skoolathome.schema.sheet import Item, Sheet

FONTS = Path(__file__).parent / "fonts"
FORMAT_LABELS = {"exercises": "Exercices", "test_revision": "Révision", "quick_quiz": "Interro flash",
                 "lesson_recap": "Cours en clair"}
LINE = 6.2            # mm, a line of text
WRITING_LINE = 8.5    # mm, a ruled line to write on
OPEN_LINES = 6        # lines for a written answer
WORKING_LINES = 3     # lines for a computation
MUTED = (110, 110, 110)

# ---------------------------------------------------------------- formulas as text

_SYMBOLS = {
    "times": "×", "div": "÷", "cdot": "·", "pm": "±", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "pi": "π", "infty": "∞", "degree": "°", "circ": "°",
    "ldots": "…", "dots": "…", "cdots": "…", "rightarrow": "→", "to": "→", "Rightarrow": "⇒",
    "Leftrightarrow": "⇔", "in": "∈", "notin": "∉", "angle": "∠", "alpha": "α", "beta": "β",
    "gamma": "γ", "Delta": "Δ", "theta": "θ", "lambda": "λ", "mu": "μ", "parallel": "∥", "perp": "⊥",
    "quad": "  ", "qquad": "   ", "left": "", "right": "", "displaystyle": "", "%": "%", ",": " ",
    ";": " ", ":": " ", "!": "", " ": " ", "{": "(", "}": ")", "$": "$",
}
_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_GROUP = r"\{([^{}]*)\}"
_SIMPLE = re.compile(r"^[\w.,]+$")


def _wrapped(text: str) -> str:
    return text if _SIMPLE.match(text) else f"({text})"


def _script(text: str, table: dict, mark: str) -> str:
    translated = text.translate(table)
    return translated if all(c in "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎" for c in translated) else f"{mark}{_wrapped(text)}"


def formula(latex: str) -> str:
    """A school formula in plain text: \\frac{3}{4} is 3/4, x^{2} is x², \\sqrt{2} is √2."""
    text = re.sub(r"\^\s*\{?\\circ\}?", "°", latex)  # 45^\circ is 45°
    for _ in range(10):  # from the innermost group out
        before = text
        text = re.sub(r"\\[dt]?frac" + _GROUP + _GROUP, lambda m: f"{_wrapped(m[1])}/{_wrapped(m[2])}", text)
        text = re.sub(r"\\sqrt" + _GROUP, lambda m: "√" + _wrapped(m[1]), text)
        text = re.sub(r"\\(?:text|mathrm|mathbf|textbf|mathit|operatorname|mbox|overline|vec)" + _GROUP,
                      lambda m: m[1], text)
        text = re.sub(r"\\(?:widehat|hat)" + _GROUP, lambda m: "∠" + m[1], text)
        text = re.sub(r"\^" + _GROUP, lambda m: _script(m[1], _SUPERSCRIPT, "^"), text)
        text = re.sub(r"_" + _GROUP, lambda m: _script(m[1], _SUBSCRIPT, "_"), text)
        if text == before:
            break
    text = re.sub(r"\^(\w)", lambda m: _script(m[1], _SUPERSCRIPT, "^"), text)
    text = re.sub(r"_(\d)", lambda m: _script(m[1], _SUBSCRIPT, "_"), text)
    text = re.sub(r"\\sqrt(\w+)", lambda m: "√" + m[1], text)
    text = re.sub(r"\\([a-zA-Z]+|.)", lambda m: _SYMBOLS.get(m[1], m[1]), text)
    return " ".join(text.replace("{", "").replace("}", "").split())


def plain(text: str) -> str:
    """A prompt or a correction as the PDF prints it: the formulas between $ signs as text."""
    parts = text.split("$")
    shown = "".join(formula(part) if index % 2 else part for index, part in enumerate(parts))
    return "".join(c for c in shown if c in "\n\t" or unicodedata.category(c)[0] != "C")


# ------------------------------------------------------------------------ the page

_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre")
_DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def _day(value: date) -> str:
    return f"{_DAYS[value.weekday()]} {value.day} {_MONTHS[value.month - 1]}"


def file_name(sheet: Sheet) -> str:
    """An ASCII name for the downloaded file: « Fiche - Preparer le controle.pdf »."""
    ascii_title = unicodedata.normalize("NFKD", sheet.title).encode("ascii", "ignore").decode()
    words = re.sub(r"[^A-Za-z0-9 _-]", " ", ascii_title).split()
    return ("Fiche - " + " ".join(words)[:80]).strip(" -") + ".pdf"


class _Paper(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.add_font("dejavu", "", FONTS / "DejaVuSans.ttf")
        self.add_font("dejavu", "B", FONTS / "DejaVuSans-Bold.ttf")
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, 18)
        self.set_title("Fiche de révision")

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("dejavu", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"Skoolathome · page {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0)

    # -- helpers

    def text_block(self, text: str, *, size: float = 11, bold: bool = False, muted: bool = False,
                   indent: float = 0, height: float = LINE) -> None:
        self.set_font("dejavu", "B" if bold else "", size)
        if muted:
            self.set_text_color(*MUTED)
        self.set_x(self.l_margin + indent)
        self.multi_cell(0, height, text, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(0)

    def lines_height(self, text: str, indent: float, size: float = 11) -> float:
        self.set_font("dejavu", "", size)
        width = self.w - self.l_margin - self.r_margin - indent
        return len(self.multi_cell(width, LINE, text, align="L", dry_run=True, output="LINES")) * LINE

    def ruled(self, count: int, indent: float) -> None:
        self.set_draw_color(170)
        for _ in range(count):
            self.ln(WRITING_LINE)
            self.line(self.l_margin + indent, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_draw_color(0)
        self.ln(2)

    def keep_together(self, height: float) -> None:
        if self.get_y() + height > self.page_break_trigger:
            self.add_page()


def _prompt(item: Item) -> str:
    text = plain(item.prompt)
    return text.replace("___", "______________") if item.type == "fill_blank" else text


def _answer_room(item: Item) -> float:
    """Height of the space to answer, to keep a question with its space on one page."""
    if item.type == "choice":
        return len(item.choices) * LINE + 2
    if item.type == "matching":
        return (len(item.left) + 2) * LINE
    if item.type == "open":
        return OPEN_LINES * WRITING_LINE + 2
    if item.type in ("number", "fraction"):
        return (WORKING_LINES + 1) * WRITING_LINE + 2
    if item.type == "fill_blank":
        return 2
    return WRITING_LINE + 4


def _question(pdf: _Paper, number: int, item: Item) -> None:
    indent = 8
    prompt = _prompt(item)
    pdf.keep_together(pdf.lines_height(prompt, indent) + _answer_room(item) + 4)
    top = pdf.get_y()
    pdf.set_font("dejavu", "B", 11)
    pdf.cell(indent, LINE, f"{number}.")
    pdf.set_y(top)
    pdf.text_block(prompt, indent=indent)
    if item.type == "choice":
        for choice in item.choices:
            pdf.text_block(f"☐  {plain(choice)}", indent=indent + 3)
    elif item.type == "matching":
        for left in item.left:
            pdf.text_block(f"•  {plain(left)}  →  ____________________", indent=indent + 3)
        pdf.text_block("À choisir parmi : " + "  ·  ".join(plain(c) for c in item.choices), size=10, muted=True,
                       indent=indent + 3)
    elif item.type == "open":
        pdf.ruled(OPEN_LINES, indent)
    elif item.type in ("number", "fraction"):
        # Room to compute: on paper, the steps are written down.
        pdf.text_block("Calcul :", size=10, muted=True, indent=indent)
        pdf.ruled(WORKING_LINES, indent)
        pdf.text_block("Résultat : ______________________", indent=indent)
    elif item.type != "fill_blank":
        pdf.ln(2)
        pdf.text_block("Réponse : ________________________________________", indent=indent)
    pdf.ln(4)


def _correction(pdf: _Paper, sheet: Sheet) -> None:
    pdf.add_page()
    pdf.text_block("Corrigé", size=15, bold=True)
    pdf.text_block("Pour le parent, ou pour se corriger une fois la fiche finie.", size=10, muted=True)
    pdf.ln(3)
    for number, item in enumerate(sheet.items, start=1):
        answer = ", ".join(item.answer) if isinstance(item.answer, list) else item.answer
        lines = []
        if item.type != "open" and answer:
            lines.append(("B", f"{number}.  {plain(answer)}"))
        else:
            lines.append(("B", f"{number}.  (réponse rédigée)"))
        if item.solution:
            lines.append(("", plain(item.solution)))
        if item.parent_guide and item.parent_guide.common_mistake:
            lines.append(("M", "Erreur fréquente : " + plain(item.parent_guide.common_mistake)))
        height = sum(pdf.lines_height(text, 6, 10) for _, text in lines) + 3
        pdf.keep_together(height)
        for style, text in lines:
            pdf.text_block(text, size=10, bold=style == "B", muted=style == "M", indent=0 if style == "B" else 6,
                           height=5.4)
        pdf.ln(2.5)


def sheet_pdf(sheet: Sheet) -> bytes:
    pdf = _Paper()
    pdf.add_page()
    pdf.text_block(f"{sheet.subject} · {FORMAT_LABELS.get(sheet.type, sheet.type)}", size=10, muted=True)
    pdf.text_block(plain(sheet.title), size=17, bold=True, height=8)
    labels = [sheet.source_labels[key] for key in sheet.sources if key in sheet.source_labels]
    if labels:
        pdf.text_block("D'après " + ", ".join(labels), size=10, muted=True)
    when = f"Durée : environ {sheet.estimated_minutes} min"
    if sheet.due_by:
        when += f" · à faire avant le {_day(sheet.due_by)}"
    if sheet.estimated_minutes >= 30:
        when += " · en une ou plusieurs fois"
    pdf.text_block(when, size=10, muted=True)
    pdf.ln(3)
    pdf.text_block("Prénom : ____________________        Date : ______________")
    pdf.ln(4)
    if sheet.recap is not None:
        recap = sheet.recap
        pdf.text_block("Le cours en clair", size=13, bold=True)
        pdf.text_block(plain(recap.summary))
        pdf.ln(2)
        pdf.text_block("Exemple : " + plain(recap.example.statement), bold=True, size=10.5)
        pdf.text_block(plain(recap.example.solution), size=10.5, indent=4)
        pdf.ln(2)
        pdf.text_block("À retenir", bold=True, size=10.5)
        for point in recap.key_points:
            pdf.text_block("•  " + plain(point), size=10.5, indent=4)
        pdf.ln(4)
    pdf.text_block(f"Questions ({len(sheet.items)})", size=13, bold=True)
    pdf.ln(2)
    for number, item in enumerate(sheet.items, start=1):
        _question(pdf, number, item)
    _correction(pdf, sheet)
    return bytes(pdf.output())
